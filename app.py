# ==========================================
# 🌊 AI FLOOD MAPPING SYSTEM (FINAL UI)
# ==========================================

import streamlit as st
import ee
import geemap
import geopandas as gpd
import tempfile
import zipfile
import os
import pandas as pd

# ==========================================
# 🔐 AUTH
# ==========================================


service_account = st.secrets["gcp_service_account"]["client_email"]

credentials = ee.ServiceAccountCredentials(
    service_account,
    key_data=st.secrets["gcp_service_account"]
)

ee.Initialize(credentials)

# ==========================================
# 🧠 AI INPUT HANDLER
# ==========================================

def process_input(input_type, coords, uploaded_file):
    if input_type == "Coordinates":
        lon_min, lat_min, lon_max, lat_max = coords
        
        # AI correction
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
        area_size = gdf.area.mean()

    return aoi, area_size


# ==========================================
# 🧠 AI PREPROCESS ENGINE
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

    # AI classification
    terrain_type = ee.Algorithms.If(avg_slope.gt(8), "Mountain",
                    ee.Algorithms.If(avg_slope.gt(3), "Mixed", "Flat"))

    # AI parameter tuning
    k = ee.Algorithms.If(avg_slope.gt(8), 1.5,
        ee.Algorithms.If(avg_slope.gt(3), 1.2, 1.0))

    smoothing = ee.Algorithms.If(avg_slope.gt(8), 60, 40)

    return avg_slope, terrain_type, ee.Number(k), smoothing


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
# 🔘 BUTTONS (3 STAGES)
# ==========================================

if "aoi" not in st.session_state:
    st.session_state.aoi = None

if "params" not in st.session_state:
    st.session_state.params = None


# ---------- INPUT ----------
if st.sidebar.button("1️⃣ Process Input"):
    aoi, area_size = process_input(input_type, coords, uploaded_file)
    st.session_state.aoi = aoi
    st.success(f"Input Processed | Area Size: {area_size:.4f}")


# ---------- PREPROCESS ----------
if st.sidebar.button("2️⃣ AI Preprocess"):
    if st.session_state.aoi is None:
        st.error("Process input first")
    else:
        avg_slope, terrain_type, k, smoothing = ai_preprocess(st.session_state.aoi)

        st.session_state.params = {
            "slope": avg_slope.getInfo(),
            "terrain": terrain_type.getInfo(),
            "k": k,
            "smooth": smoothing
        }

        st.success(f"Terrain: {terrain_type.getInfo()} | Slope: {avg_slope.getInfo():.2f}")


# ---------- GENERATE ----------
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

        mean = ee.Number(stats.get('VV_mean'))
        std  = ee.Number(stats.get('VV_stdDev'))

        threshold = mean.add(std.multiply(params["k"]))

        flood = change.gt(threshold)

        terrain = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))
        flood = flood.updateMask(terrain.lt(6))

        cleaned = flood.focal_max(1).focal_min(1)

        Map = geemap.Map()
        Map.addLayer(cleaned.clip(aoi), {'palette': ['blue']}, 'Flood')

        Map.centerObject(aoi, 9)
        Map.to_streamlit(height=600)

        st.success("Flood Map Generated")
