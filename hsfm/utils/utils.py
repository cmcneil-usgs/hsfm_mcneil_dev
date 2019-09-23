import hvplot
import hvplot.xarray
import hvplot.pandas
import rasterio
import xarray as xr
import cartopy.crs as ccrs
import geoviews as gv
from geoviews import opts
import glob
import holoviews as hv
from osgeo import gdal
import os
import pandas as pd
import panel as pn
import numpy as np
import PIL
import shutil
import subprocess
from subprocess import Popen, PIPE, STDOUT
import time
import utm

hv.extension('bokeh')

import hsfm.io
import hsfm.geospatial


def run_command(command, verbose=False, log_directory=None, shell=False):
    
    p = Popen(command,
              stdout=PIPE,
              stderr=STDOUT,
              shell=shell)
    
    if log_directory != None:
        log_file_name = os.path.join(log_directory,command[0]+'_log.txt')
        hsfm.io.create_dir(log_directory)
    
        with open(log_file_name, "w") as log_file:
            
            while p.poll() is None:
                line = (p.stdout.readline()).decode('ASCII').rstrip('\n')
                if verbose == True:
                    print(line)
                log_file.write(line)
        return log_file_name
    
    else:
        while p.poll() is None:
            line = (p.stdout.readline()).decode('ASCII').rstrip('\n')
            if verbose == True:
                print(line)
        
        


def download_srtm(LLLON,LLLAT,URLON,URLAT,
                  output_directory='./data/reference_dem/',
                  verbose=True,
                  cleanup=False):
    # TODO
    # - Add function to determine extent automatically from input cameras
    # - Make geoid adjustment and converstion to UTM optional
    # - Preserve wgs84 dem
    import elevation
    
    run_command(['eio', 'selfcheck'], verbose=verbose)
    print('Downloading SRTM DEM data...')

    hsfm.io.create_dir(output_directory)

    cache_dir=output_directory
    product='SRTM3'
    dem_bounds = (LLLON, LLLAT, URLON, URLAT)

    elevation.seed(bounds=dem_bounds,
                   cache_dir=cache_dir,
                   product=product,
                   max_download_tiles=999)

    tifs = glob.glob(os.path.join(output_directory,'SRTM3/cache/','*tif'))
    
    vrt_file_name = os.path.join(output_directory,'SRTM3/cache/srtm.vrt')
    
    call = ['gdalbuildvrt', vrt_file_name]
    call.extend(tifs)
    run_command(call, verbose=verbose)

    
    ds = gdal.Open(vrt_file_name)
    vrt_subset_file_name = os.path.join(output_directory,'SRTM3/cache/srtm_subset.vrt')
    ds = gdal.Translate(vrt_subset_file_name,
                        ds, 
                        projWin = [LLLON, URLAT, URLON, LLLAT])
                        
    
    # Adjust from EGM96 geoid to WGS84 ellipsoid
    adjusted_vrt_subset_file_name_prefix = os.path.join(output_directory,'SRTM3/cache/srtm_subset')
    call = ['dem_geoid',
            '--reverse-adjustment',
            vrt_subset_file_name, 
            '-o', 
            adjusted_vrt_subset_file_name_prefix]
    run_command(call, verbose=verbose)
    
    adjusted_vrt_subset_file_name = adjusted_vrt_subset_file_name_prefix+'-adj.tif'

    # Get UTM EPSG code
    epsg_code = hsfm.geospatial.wgs_lon_lat_to_epsg_code(LLLON, LLLAT)
    
    # Convert to UTM
    utm_vrt_subset_file_name = os.path.join(output_directory,'SRTM3/cache/srtm_subset_utm_geoid_adj.tif')
    call = 'gdalwarp -co COMPRESS=LZW -co TILED=YES -co BIGTIFF=IF_SAFER -dstnodata -9999 -r cubic -t_srs EPSG:' + epsg_code
    call = call.split()
    call.extend([adjusted_vrt_subset_file_name,utm_vrt_subset_file_name])
    run_command(call, verbose=verbose)
    
    # Cleanup
    if cleanup == True:
        print('Cleaning up...','Reference DEM available at', out)
        out = os.path.join(output_directory,os.path.split(utm_vrt_subset_file_name)[-1])
        os.rename(utm_vrt_subset_file_name, out)
        shutil.rmtree(os.path.join(output_directory,'SRTM3/'))
    
        return out
        
    else:
        return utm_vrt_subset_file_name
        
def pick_headings(image_directory, camera_positions_file_name, subset, delta=0.015):
    df = hsfm.core.select_images_for_download(camera_positions_file_name, subset)
    
    image_file_paths = sorted(glob.glob(os.path.join(image_directory, '*.tif')))
    lons = df['Longitude'].values
    lats = df['Latitude'].values

    headings = []
    for i, v in enumerate(lats):
        image_file_name = image_file_paths[i]
        camera_center_lon = lons[i]
        camera_center_lat = lats[i]

        heading = pick_heading_from_map(image_file_name,
                                        camera_center_lon,
                                        camera_center_lat,
                                        dx= delta,
                                        dy= delta)
        headings.append(heading)

    df['heading'] = headings

    return df

def scale_down_number(number, threshold=1000):
    while number > threshold:
        number = number / 2
    number = int(number)
    return number
    
def pick_heading_from_map(image_file_name,
                          camera_center_lon,
                          camera_center_lat,
                          dx = 0.015,
                          dy = 0.015):
                          

    # Google Satellite tiled basemap imagery url
    url = 'https://mt1.google.com/vt/lyrs=s&x={X}&y={Y}&z={Z}'
    
    # TODO
    # # allow large images to be plotted or force resampling to thumbnail
    # # load the image with xarray and plot with hvplot to handle larger images
    img, subplot_width, subplot_height = hv_plot_raster(image_file_name)

    # create the extent of the bounding box
    extents = (camera_center_lon-dx, 
               camera_center_lat-dy, 
               camera_center_lon+dx, 
               camera_center_lat+dy)


    # run the tile server
    tiles = gv.WMTS(url, extents=extents)

    points = gv.Points([(camera_center_lon,
                         camera_center_lat,
                         'camera_center')], 
                         vdims='location')

    point_stream = hv.streams.PointDraw(source=points)

    base_map = (tiles * points).opts(opts.Points(width=subplot_width, 
                                                 height=subplot_height, 
                                                 size=10, 
                                                 color='black', 
                                                 tools=["hover"]))

    row = pn.Row(img, base_map)

    server = row.show(threaded=True)

    condition = True
    while condition == True:
        try:
            if len(point_stream.data['x']) == 2:
                server.stop()
                condition = False
        except:
            pass

    projected = gv.operation.project_points(point_stream.element,
                                            projection=ccrs.PlateCarree())
    df = projected.dframe()
    df['location'] = ['camera_center', 'flight_direction']
    
    heading_lon = df.x[1]
    heading_lat = df.y[1]
    
    heading = hsfm.geospatial.calculate_heading(camera_center_lon,
                                                camera_center_lat,
                                                heading_lon,
                                                heading_lat)
    
    return heading
                                 
def difference_dems(dem_file_name_a,
                    dem_file_name_b,
                    verbose=False):
    
    file_path, file_name, file_extension = hsfm.io.split_file(dem_file_name_a)
    
    output_directory_and_prefix = os.path.join(file_path,file_name)
    
    call = ['geodiff',
            '--absolute',
            dem_file_name_a,
            dem_file_name_b,
            '-o', output_directory_and_prefix]
            
    run_command(call, verbose=verbose)
    
    output_file_name = output_directory_and_prefix+'-diff.tif'
    
    return output_file_name

def dem_align_custom(reference_dem,
                     dem_to_be_aligned,
                     mode='nuth',
                     max_offset = 1000,
                     verbose=False,
                     log_directory=None):
    
    call = ['dem_align.py',
            '-max_offset',str(max_offset),
            '-mode', mode,
            reference_dem,
            dem_to_be_aligned]
            
    log_file_name = run_command(call, verbose=verbose, log_directory=log_directory)

    with open(log_file_name, 'r') as file:
        output_plot_file_name = file.read().split()[-3]
    dem_difference_file_name = glob.glob(os.path.split(output_plot_file_name)[0]+'/*_align_diff.tif')[0]
    aligned_dem_file_name = glob.glob(os.path.split(output_plot_file_name)[0]+'/*align.tif')[0]
    
    return dem_difference_file_name , aligned_dem_file_name
    

def rescale_geotif(geotif_file_name,
                   output_file_name=None,
                   scale=1,
                   verbose=False):
                   
    percent = str(100/scale) +'%'
    
    if output_file_name is None:
        file_path, file_name, file_extension = hsfm.io.split_file(geotif_file_name)
        output_file_name = os.path.join(file_path, 
                                        file_name+'_sub'+str(scale)+file_extension)
    
    call = ['gdal_translate',
            '-of','GTiff',
            '-co','TILED=YES',
            '-co','COMPRESS=LZW',
            '-co','BIGTIFF=IF_SAFER',
            '-outsize',percent,percent,
            geotif_file_name,
            output_file_name]
            
    run_command(call, verbose=verbose)
    
    return output_file_name

def optimize_geotif(geotif_file_name,
                    output_file_name=None,
                    verbose=False):
                   

    if output_file_name is None:
        file_path, file_name, file_extension = hsfm.io.split_file(geotif_file_name)
        output_file_name = os.path.join(file_path, 
                                        file_name+'_optimized'+file_extension)
    
    call = ['gdal_translate',
            '-of','GTiff',
            '-co','TILED=YES',
            '-co','COMPRESS=LZW',
            '-co','BIGTIFF=IF_SAFER',
            geotif_file_name,
            output_file_name]
            
    run_command(call, verbose=verbose)
    
    return output_file_name
    
def launch_fiducial_picker(hv_image, subplot_width, subplot_height):
    points = hv.Points([])
    point_stream = hv.streams.PointDraw(source=points)

    app = (hv_image * points).opts(hv.opts.Points(width=subplot_width,
                                                  height=subplot_height,
                                                  size=5,
                                                  color='blue',
                                                  tools=["hover"]))

    panel = pn.panel(app)

    server = panel.show(threaded=True)

    condition = True
    while condition == True: 
        try:
            if len(point_stream.data['x']) == 4:
                server.stop()
                condition = False
        except:
            pass
    
    df = point_stream.element.dframe()
    
    left_fiducial   = (df.x[0],df.y[0])
    top_fiducial    = (df.x[1],df.y[1])
    right_fiducial  = (df.x[2],df.y[2])
    bottom_fiducial = (df.x[3],df.y[3])
    
    fiducials = [left_fiducial, top_fiducial, right_fiducial, bottom_fiducial]
    principal_point = hsfm.core.determine_principal_point(fiducials[0],
                                                          fiducials[1],
                                                          fiducials[2],
                                                          fiducials[3])
    
    
    
    
    return fiducials, principal_point
    
def hv_plot_raster(image_file_name):
    src = rasterio.open(image_file_name)

    subplot_width  = scale_down_number(src.shape[0])
    subplot_height = scale_down_number(src.shape[1])

    da = xr.open_rasterio(src)

    hv_image = da.sel(band=1).hvplot.image(rasterize=True,
                                      width=subplot_width,
                                      height=subplot_height,
                                      flip_yaxis=True,
                                      colorbar=False,
                                      cmap='gray')
                                      
    return hv_image, subplot_width, subplot_height
    
def pick_fiducials(image_file_name):
    
    hv_image, subplot_width, subplot_height = hv_plot_raster(image_file_name)
    fiducials, principal_point = launch_fiducial_picker(hv_image,
                                                        subplot_width,
                                                        subplot_height)
    
    intersection_angle = hsfm.core.determine_intersection_angle(fiducials)
    
    return principal_point, intersection_angle