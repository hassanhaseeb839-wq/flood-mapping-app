# ==========================================
# 🌊 AI FLOOD MAPPING SYSTEM (FINAL CLEAN)
# ==========================================

import streamlit as st
import ee
import json
import geemap
import geopandas as gpd
import tempfile
import zipfile
import os
import pandas as pd

# ==========================================
# 🔐 EARTH ENGINE AUTH (FIXED)
# ==========================================

if "ee_initialized" not in st.session_state:
    service_account = st.secrets["gcp_service_account"]["client_email"]

    credentials = ee.ServiceAccountCredentials(
        service_account,
        key_data=json.dumps(st.secrets["gcp_service_account"])
    )

    ee.Initialize(credentials)
    st.session_state.ee_initialized = True

# ==========================================
st.set_page_config(layout="wide")
st.title("🌊 AI Universal Flood Mapping System")

# ==========================================
# 🧠 INPUT HANDLER
# ==========================================

def process_input(input_type, coords, uploaded_file):
    if input_type == "Coordinates":
        lon_min, lat_min, lon_max, lat_max = coords

        if lon_min > lon_max:
            lon_min, lon_max = lon_max, lon_min
        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min

        aoi = ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max])
        area_size = (lon_max - lon_min) * (lat_max - lat_min)

    else:
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "shape.zip")

        with open(zip_path, "wb") as f:
            f.write(uploaded_file.read())

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        shp = [f for f in os.listdir(temp_dir) if f.endswith(".shp")][0]
        gdf = gpd.read_file(os.path.join(temp_dir, shp))

        aoi = geemap.geopandas_to_ee(gdf)
        area_size = float(gdf.area.mean())

    return aoi, area_size

# ==========================================
# 🧠 AI PREPROCESS (FIXED)
# ==========================================

def ai_preprocess(aoi):

    terrain = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))

    avg_slope = ee.Number(
        terrain.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=aoi,
            scale=90,
            maxPixels=1e13
        ).get('slope')
    )

    slope_val = avg_slope.getInfo()

    if slope_val > 8:
        terrain_type = "Mountain"
        k = 1.5
        smoothing = 60
    elif slope_val > 3:
        terrain_type = "Mixed"
        k = 1.2
        smoothing = 40
    else:
        terrain_type = "Flat"
        k = 1.0
        smoothing = 40

    return slope_val, terrain_type, k, smoothing

# ==========================================
# 🔹 UI INPUT
# ==========================================

st.sidebar.header("📥 Input")

input_type = st.sidebar.radio("Select Input", ["Coordinates", "Shapefile"])

coords = None
uploaded_file = None

if input_type == "Coordinates":
    lon_min = st.sidebar.number_input("Min Lon", value=56.0)
    lat_min = st.sidebar.number_input("Min Lat", value=25.5)
    lon_max = st.sidebar.number_input("Max Lon", value=57.5)
    lat_max = st.sidebar.number_input("Max Lat", value=26.7)

    coords = (lon_min, lat_min, lon_max, lat_max)

else:
    uploaded_file = st.sidebar.file_uploader("Upload .zip shapefile", type=["zip"])

st.sidebar.header("📅 Dates")

before_start = st.sidebar.date_input("Before Start")
before_end   = st.sidebar.date_input("Before End")
after_start  = st.sidebar.date_input("After Start")
after_end    = st.sidebar.date_input("After End")

# ==========================================
# SESSION STATE
# ==========================================

if "aoi" not in st.session_state:
    st.session_state.aoi = None

if "params" not in st.session_state:
    st.session_state.params = None

# ==========================================
# BUTTONS
# ==========================================

if st.sidebar.button("1️⃣ Process Input"):
    aoi, area_size = process_input(input_type, coords, uploaded_file)
    st.session_state.aoi = aoi
    st.success(f"Input Processed | Area Size: {area_size:.4f}")

if st.sidebar.button("2️⃣ AI Preprocess"):
    if st.session_state.aoi is None:
        st.error("Process input first")
    else:
        slope, terrain, k, smooth = ai_preprocess(st.session_state.aoi)

        st.session_state.params = {
            "slope": slope,
            "terrain": terrain,
            "k": k,
            "smooth": smooth
        }

        st.success(f"Terrain: {terrain} | Slope: {slope:.2f}")

if st.sidebar.button("3️⃣ Generate Flood Map"):

    if st.session_state.aoi is None or st.session_state.params is None:
        st.error("Complete previous steps first")

    else:
        aoi = st.session_state.aoi
        params = st.session_state.params

        def reduce_speckle(img):
            return img.focal_median(params["smooth"], 'square', 'meters')

        def get_s1(start, end):
            return (ee.ImageCollection("COPERNICUS/S1_GRD")
                    .filterBounds(aoi)
                    .filterDate(str(start), str(end))
                    .filter(ee.Filter.eq('instrumentMode', 'IW'))
                    .select('VV')
                    .map(reduce_speckle))

        before = get_s1(before_start, before_end).median()
        after  = get_s1(after_start, after_end).median()

        change = before.subtract(after)

        stats = change.reduceRegion(
            reducer=ee.Reducer.mean().combine(
                reducer2=ee.Reducer.stdDev(),
                sharedInputs=True
            ),
            geometry=aoi,
            scale=30,
            maxPixels=1e13
        )

        mean = ee.Number(stats.get('VV_mean')).getInfo()
        std  = ee.Number(stats.get('VV_stdDev')).getInfo()

        threshold = mean + std * params["k"]

        flood = change.gt(threshold)

        terrain = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))
        flood = flood.updateMask(terrain.lt(6))

        cleaned = flood.focal_max(1).focal_min(1)

        Map = geemap.Map()
        Map.addLayer(cleaned.clip(aoi), {'palette': ['blue']}, 'Flood')

        Map.centerObject(aoi, 9)
        Map.to_streamlit(height=600)

        st.success("Flood Map Generated ✅")
