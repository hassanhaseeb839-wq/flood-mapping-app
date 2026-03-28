import streamlit as st
import ee
import json
import geopandas as gpd
import tempfile
import zipfile
import os
import folium
from streamlit_folium import st_folium

# ==========================================
# 🔐 EARTH ENGINE AUTH
# ==========================================

if "ee_initialized" not in st.session_state:
    service_account_info = dict(st.secrets["gcp_service_account"])

    credentials = ee.ServiceAccountCredentials(
        service_account_info["client_email"],
        key_data=json.dumps(service_account_info)
    )

    ee.Initialize(credentials)
    st.session_state.ee_initialized = True

# ==========================================
# 🎨 UI
# ==========================================

st.set_page_config(layout="wide")
st.title("🌊 AI Flood Mapping System")

# ==========================================
# 📦 SESSION STATE (SAFE INIT)
# ==========================================

if "aoi" not in st.session_state:
    st.session_state.aoi = None

if "flood_map" not in st.session_state:
    st.session_state.flood_map = None

if "before" not in st.session_state:
    st.session_state.before = None

if "after" not in st.session_state:
    st.session_state.after = None

# ==========================================
# 📥 INPUT
# ==========================================

st.sidebar.header("📥 Input")

input_type = st.sidebar.radio("Select Input", ["Coordinates", "Shapefile"])

coords = None
uploaded_file = None

if input_type == "Coordinates":
    lon_min = st.sidebar.number_input("Min Lon", value=104.5)
    lat_min = st.sidebar.number_input("Min Lat", value=15.0)
    lon_max = st.sidebar.number_input("Max Lon", value=105.5)
    lat_max = st.sidebar.number_input("Max Lat", value=15.8)
    coords = (lon_min, lat_min, lon_max, lat_max)

else:
    uploaded_file = st.sidebar.file_uploader("Upload shapefile (.zip)", type=["zip"])

# ==========================================
# 📅 DATES
# ==========================================

st.sidebar.header("📅 Dates")

before_start = st.sidebar.date_input("Before Start")
before_end   = st.sidebar.date_input("Before End")
after_start  = st.sidebar.date_input("After Start")
after_end    = st.sidebar.date_input("After End")

# ==========================================
# 🧠 PROCESS INPUT
# ==========================================

def process_input():
    if input_type == "Coordinates":
        lon_min, lat_min, lon_max, lat_max = coords
        return ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max])

    else:
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "shape.zip")

        with open(zip_path, "wb") as f:
            f.write(uploaded_file.read())

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        shp = [f for f in os.listdir(temp_dir) if f.endswith(".shp")][0]
        gdf = gpd.read_file(os.path.join(temp_dir, shp))

        return ee.Geometry.Polygon(gdf.geometry.iloc[0].__geo_interface__['coordinates'])

# ==========================================
# 🚀 GENERATE FLOOD MAP
# ==========================================

if st.sidebar.button("🚀 Generate Flood Map"):

    if input_type == "Shapefile" and uploaded_file is None:
        st.error("Upload shapefile first")

    else:
        aoi = process_input()
        st.session_state.aoi = aoi

        def get_s1(start, end):
            return (ee.ImageCollection("COPERNICUS/S1_GRD")
                    .filterBounds(aoi)
                    .filterDate(str(start), str(end))
                    .filter(ee.Filter.eq('instrumentMode', 'IW'))
                    .select('VV')
                    .median())

        before = get_s1(before_start, before_end)
        after  = get_s1(after_start, after_end)

        # Save for later use
        st.session_state.before = before
        st.session_state.after = after

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

        threshold = mean + std * 0.5

        flood = change.gt(threshold)

        cleaned = flood.focal_max(1).focal_min(1)

        st.session_state.flood_map = cleaned

        st.success("Flood Map Generated ✅")

# ==========================================
# 🗺️ DISPLAY MAP
# ==========================================

if (
    st.session_state.flood_map is not None and
    st.session_state.before is not None and
    st.session_state.after is not None
):

    aoi = st.session_state.aoi
    flood_img = st.session_state.flood_map
    before = st.session_state.before
    after = st.session_state.after

    m = folium.Map(location=[15.5, 104.5], zoom_start=7, control_scale=True)

    # BEFORE layer
    before_map = before.clip(aoi).getMapId({'min': -25, 'max': 0})
    folium.TileLayer(
        tiles=before_map['tile_fetcher'].url_format,
        name='Before',
        overlay=True
    ).add_to(m)

    # AFTER layer
    after_map = after.clip(aoi).getMapId({'min': -25, 'max': 0})
    folium.TileLayer(
        tiles=after_map['tile_fetcher'].url_format,
        name='After',
        overlay=True
    ).add_to(m)

    # FLOOD layer
    flood_map = flood_img.clip(aoi).getMapId({'palette': ['red']})
    folium.TileLayer(
        tiles=flood_map['tile_fetcher'].url_format,
        name='Flood',
        overlay=True
    ).add_to(m)

    folium.LayerControl().add_to(m)

    # Clickable coordinates
    m.add_child(folium.LatLngPopup())

    output = st_folium(m, width=1000, height=600)

    if output and output.get("last_clicked"):
        lat = output["last_clicked"]["lat"]
        lon = output["last_clicked"]["lng"]
        st.info(f"📍 Lat: {lat:.5f}, Lon: {lon:.5f}")
