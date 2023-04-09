# Author: Md Fahim Hasan
# Email: Fahim.Hasan@colostate.edu

import os
import pickle
import fiona
import numpy as np
import pandas as pd
from glob import glob
from osgeo import gdal
import geopandas as gpd
from rasterio.mask import mask
from shapely.geometry import mapping, shape
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, accuracy_score, classification_report, \
    precision_score, recall_score, f1_score
from System_operations import makedirs
from Raster_operations import shapefile_to_raster, mosaic_rasters, read_raster_arr_object, \
    write_raster, clip_resample_raster_cutline, resample_reproject
from ML_operations import reindex_df

import warnings

warnings.simplefilter(action='ignore', category=FutureWarning)  # to ignore future warning coming from pandas
warnings.filterwarnings(action='ignore')

No_Data_Value = -9999
referenceraster = '../Data/Reference_rasters_shapes/Global_continents_ref_raster.tif'


def combine_georeferenced_subsidence_polygons(input_polygons_dir, joined_subsidence_polygons,
                                              search_criteria='*Subsidence*.shp', skip_polygon_processing=True):
    """
    Combining georeferenced subsidence polygons.

    Parameters:
    input_polygons_dir : Input subsidence polygons' directory.
    joined_subsidence_polygons : Output joined subsidence polygon filepath.
    search_criteria : Search criteria for input polygons.
    skip_polygon_processing : Set False if want to process georeferenced subsidence polygons.

    Returns : Joined subsidence polygon.
    """

    global gdf
    if not skip_polygon_processing:
        subsidence_polygons = glob(os.path.join(input_polygons_dir, search_criteria))

        sep = joined_subsidence_polygons.rfind(os.sep)
        makedirs([joined_subsidence_polygons[:sep]])  # creating directory for the  prepare_subsidence_raster function

        for each in range(1, len(subsidence_polygons) + 1):
            if each == 1:
                gdf = gpd.read_file(subsidence_polygons[each - 1])

            gdf_new = gpd.read_file(subsidence_polygons[each - 1])
            add_to_gdf = pd.concat([gdf, gdf_new], ignore_index=True)
            gdf = add_to_gdf
            gdf['Class_name'] = gdf['Class_name'].astype(float)

        unique_area_name = gdf['Area_name'].unique().tolist()
        unique_area_name_code = [i + 1 for i in range(len(unique_area_name))]

        polygon_area_name_dict = {}
        for name, code in zip(unique_area_name, unique_area_name_code):
            polygon_area_name_dict[name] = code

        Area_code = []
        for index, row in gdf.iterrows():
            Area_code.append(polygon_area_name_dict[row['Area_name']])

        gdf['Area_code'] = pd.Series(Area_code)

        gdf.to_file(joined_subsidence_polygons)

        pickle.dump(polygon_area_name_dict, open('../Model Run/LOO_Test/InSAR_Data/polygon_area_name_dict.pkl',
                                                 mode='wb+'))

    else:
        joined_subsidence_polygons = '../Model Run/LOO_Test/InSAR_Data/georef_subsidence_polygons.shp'
        polygon_area_name_dict = pickle.load(open('../Model Run/LOO_Test/InSAR_Data/polygon_area_name_dict.pkl',
                                                  mode='rb'))

    return joined_subsidence_polygons, polygon_area_name_dict


def substitute_area_code_on_raster(input_raster, value_to_substitute, output_raster):
    """
    Substitute raster values with area code for InSAR produced subsidence rasters (California, Arizona, Quetta, Qazvin,
    China_Hebei, China_Hefei, Colorado, Coastal_subsidence etc.)

    Parameters:
    input_raster : Input subsidence raster filepath.
    value_to_substitute : Area code that will substitute raster values.
    output_raster : Filepath of output raster.

    Returns : Raster with values substituted with area code.
    """
    raster_arr, raster_file = read_raster_arr_object(input_raster)

    raster_arr = np.where(np.isnan(raster_arr), raster_arr, value_to_substitute)

    area_coded_raster = write_raster(raster_arr, raster_file, raster_file.transform, output_raster)

    return area_coded_raster


def reclassify_resample_EGMS_insar(input_egms_dir='../InSAR_Data/Europe_EGMS/Interim_processing',
                                   search_criteria='*neg_values*.tif',
                                   output_dir='../InSAR_Data/Europe_EGMS/reclass_resample_EGMS',
                                   nodata=No_Data_Value):
    makedirs([output_dir])
    egms_datasets = glob(os.path.join(input_egms_dir, search_criteria))

    for data in egms_datasets:
        basename = os.path.basename(data)
        find_n = basename.rfind('n')  # 'n' stands for start of the the neg_values
        region_name = basename[: (find_n - 1)]

        resampled_raster = os.path.join(output_dir, f'{region_name}_resampled.tif')
        gdal.Warp(destNameOrDestDS=resampled_raster, srcDSOrSrcDSTab=data, dstSRS='EPSG:4326', xRes=0.02,
                  yRes=0.02, outputType=gdal.GDT_Float32)

        region_arr, region_file = read_raster_arr_object(resampled_raster)

        # converting mm/year to cm/year
        region_arr_cm = region_arr * 0.1
        region_arr_cm = np.where(region_arr_cm < -200, nodata, region_arr_cm)  # -200 is arbitrary big to set nodata

        # Classifying to model classes
        sub_less_1cm = 1
        sub_1cm_to_5cm = 5
        sub_greater_5cm = 10

        classified_arr = np.where(region_arr_cm >= -1, sub_less_1cm, region_arr_cm)
        classified_arr = np.where((classified_arr < -1) & (classified_arr >= -5), sub_1cm_to_5cm, classified_arr)
        classified_arr = np.where((classified_arr > nodata) & (classified_arr < -5), sub_greater_5cm, classified_arr)
        classified_arr = np.where(np.isnan(region_arr_cm), np.nan, classified_arr)

        classified_EGMS_insar = os.path.join(output_dir, f'{region_name}_reclass_resampled.tif')
        write_raster(raster_arr=classified_arr, raster_file=region_file, transform=region_file.transform,
                     outfile_path=classified_EGMS_insar)


def combine_georef_insar_subsidence_raster(input_polygons_dir='../InSAR_Data/Georeferenced_subsidence_data',
                                           joined_subsidence_polygon='../Model Run/LOO_Test/InSAR_Data/'
                                                                     'georef_subsidence_polygons.shp',
                                           insar_data_dir='../Model Run/LOO_Test/InSAR_Data/'
                                                          'interim_working_dir',
                                           interim_dir='../Model Run/LOO_Test/InSAR_Data/'
                                                       'interim_working_dir',
                                           output_dir='../Model Run/LOO_Test/InSAR_Data/'
                                                      'final_subsidence_raster',
                                           skip_polygon_processing=False,
                                           area_code_column='Area_code',
                                           final_subsidence_raster='Subsidence_area_coded.tif',
                                           polygon_search_criteria='*Subsidence*.shp', already_prepared=False,
                                           refraster=referenceraster):
    """
    Prepare area coded subsidence raster for training data by joining georeferenced polygons and insar data.

    Parameters:
    input_polygons_dir : Input subsidence polygons' directory.
    joined_subsidence_polygons : Output joined subsidence polygon filepath.
    insar_data_dir : InSAR data directory.
    interim_dir : Intermediate working directory for storing interim data.
    output_dir : Output raster directory.
    skip_polygon_processing : Set to True if polygon merge is not required.
    final_subsidence_raster : Final subsidence raster including georeferenced and insar data.
    polygon_search_criteria : Input subsidence polygon search criteria.
    insar_search_criteria : InSAR data search criteria.
    already_prepared : Set to True if subsidence raster is already prepared.
    refraster : Global Reference raster.

    Returns : Final subsidence raster to be used as training data and a subsidence area code dictionary.
    """

    if not already_prepared:
        makedirs([interim_dir, output_dir])

        # Processing georeferenced subsidence data from articles
        print('Processing area coded subsidence polygons...')
        subsidence_polygons, subsidence_areaname_dict = \
            combine_georeferenced_subsidence_polygons(input_polygons_dir, joined_subsidence_polygon,
                                                      polygon_search_criteria, skip_polygon_processing)
        print('Processed area coded subsidence polygons')

        interim_georeferenced_raster_area_coded = \
            shapefile_to_raster(subsidence_polygons, interim_dir, resolution=0.005,
                                raster_name='interim_georef_subsidence_raster_areacode_0005.tif',
                                use_attr=True, attribute=area_code_column, ref_raster=refraster, alltouched=False)
        georeferenced_raster_area_coded = \
            resample_reproject(interim_georeferenced_raster_area_coded, interim_dir,
                               raster_name='interim_georef_subsidence_raster_areacode.tif', resample=True,
                               reproject=False, both=False, resample_algorithm='near')

        # Processing EGMS InSAR data
        print('Processing EGMS InSAR data...')
        reclassify_resample_EGMS_insar()
        print('Processed EGMS InSAR data...')

        print('Processing area coded InSAR data...')
        georef_subsidence_gdf = gpd.read_file(joined_subsidence_polygon)
        num_of_georef_subsidence = len(georef_subsidence_gdf['Area_code'].unique())

        # author processed/preprocessed (7 regions)
        california_area_code = num_of_georef_subsidence + 1
        arizona_area_code = california_area_code + 1
        quetta_area_code = arizona_area_code + 1
        qazvin_area_code = quetta_area_code + 1
        hebei_area_code = qazvin_area_code + 1
        hefei_area_code = hebei_area_code + 1
        colorado_area_code = hefei_area_code + 1

        # EGMS (27 regions, hungary and romania together)
        england_london_area_code = colorado_area_code + 1
        england_manchester_sheffield_area_code = england_london_area_code + 1
        france_bordeaux_area_code = england_manchester_sheffield_area_code + 1
        germany_bleicherode_area_code = france_bordeaux_area_code + 1
        germany_cologne_area_code = germany_bleicherode_area_code + 1
        germany_flensburg_area_code = germany_cologne_area_code + 1
        germany_friedewald_area_code = germany_flensburg_area_code + 1
        germany_hamburg_area_code = germany_friedewald_area_code + 1
        germany_magdeburg_area_code = germany_hamburg_area_code + 1
        greece_alexandreia_palamas_area_code = germany_magdeburg_area_code + 1
        greece_patras_katochi_area_code = greece_alexandreia_palamas_area_code + 1
        hungary_szeged_romania_timisoara_area_code = greece_patras_katochi_area_code + 1
        italy_cerignola_campagna_area_code = hungary_szeged_romania_timisoara_area_code + 1
        italy_lustignano_area_code = italy_cerignola_campagna_area_code + 1
        italy_mazzafarro_area_code = italy_lustignano_area_code + 1
        italy_podelta_area_code = italy_mazzafarro_area_code + 1
        italy_rosarno_area_code = italy_podelta_area_code + 1
        italy_salerno_area_code = italy_rosarno_area_code + 1
        italy_schiavonea_area_code = italy_salerno_area_code + 1
        lithuania_kaunas_vinius_area_code = italy_schiavonea_area_code + 1
        netherlands_groningen_area_code = lithuania_kaunas_vinius_area_code + 1
        poland_gdansk_gdynia_area_code = netherlands_groningen_area_code + 1
        poland_katowice_area_code = poland_gdansk_gdynia_area_code + 1
        poland_lodz_area_code = poland_katowice_area_code + 1
        poland_lubin_area_code = poland_lodz_area_code + 1
        spain_murcia_area_code = poland_lubin_area_code + 1

        # Coastal from Shirzaei et al. 2021
        coastal_area_code = spain_murcia_area_code + 1

        subsidence_areaname_dict['California'] = california_area_code
        subsidence_areaname_dict['Arizona'] = arizona_area_code
        subsidence_areaname_dict['Pakistan_Quetta'] = quetta_area_code
        subsidence_areaname_dict['Iran_Qazvin'] = qazvin_area_code
        subsidence_areaname_dict['China_Hebei'] = hebei_area_code
        subsidence_areaname_dict['China_Hefei'] = hefei_area_code
        subsidence_areaname_dict['Colorado'] = colorado_area_code
        subsidence_areaname_dict['England_London'] = england_london_area_code
        subsidence_areaname_dict['England_Manchester_Sheffield'] = england_manchester_sheffield_area_code
        subsidence_areaname_dict['France_Bordeaux'] = france_bordeaux_area_code
        subsidence_areaname_dict['Germany_Bleicherode'] = germany_bleicherode_area_code
        subsidence_areaname_dict['Germany_Cologne'] = germany_cologne_area_code
        subsidence_areaname_dict['Germany_Flensburg'] = germany_flensburg_area_code
        subsidence_areaname_dict['Germany_Friedewald'] = germany_friedewald_area_code
        subsidence_areaname_dict['Germany_Hamburg'] = germany_hamburg_area_code
        subsidence_areaname_dict['Germany_Magdeburg'] = germany_magdeburg_area_code
        subsidence_areaname_dict['Greece_Alexandreia_Palamas'] = greece_alexandreia_palamas_area_code
        subsidence_areaname_dict['Greece_Patras_Katochi'] = greece_patras_katochi_area_code
        subsidence_areaname_dict['Hungary_Szeged_Romania_Timisoara'] = hungary_szeged_romania_timisoara_area_code
        subsidence_areaname_dict['Italy_Cerignola_Campagna'] = italy_cerignola_campagna_area_code
        subsidence_areaname_dict['Italy_Lustignano'] = italy_lustignano_area_code
        subsidence_areaname_dict['Italy_Mazzafarro'] = italy_mazzafarro_area_code
        subsidence_areaname_dict['Italy_PoDelta'] = italy_podelta_area_code
        subsidence_areaname_dict['Italy_Rosarno'] = italy_rosarno_area_code
        subsidence_areaname_dict['Italy_Salerno'] = italy_salerno_area_code
        subsidence_areaname_dict['Italy_Schiavonea'] = italy_schiavonea_area_code
        subsidence_areaname_dict['Lithuania_Kaunas_Vinius'] = lithuania_kaunas_vinius_area_code
        subsidence_areaname_dict['Netherlands_Groningen'] = netherlands_groningen_area_code
        subsidence_areaname_dict['Poland_Gdansk_Gdynia'] = poland_gdansk_gdynia_area_code
        subsidence_areaname_dict['Poland_Katowice'] = poland_katowice_area_code
        subsidence_areaname_dict['Poland_Lodz'] = poland_lodz_area_code
        subsidence_areaname_dict['Poland_Lubin'] = poland_lubin_area_code
        subsidence_areaname_dict['Spain_Murcia'] = spain_murcia_area_code
        subsidence_areaname_dict['Coastal'] = coastal_area_code

        california_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data' \
                                '/California_reclass_resampled.tif'
        arizona_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data/' \
                             'Arizona_reclass_resampled.tif'
        quetta_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data/' \
                            'Pakistan_Quetta_reclass_resampled.tif'
        qazvin_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data/' \
                            'Iran_Qazvin_reclass_resampled.tif'
        hebei_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data/' \
                           'China_Hebei_reclass_resampled.tif'
        hefei_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data/' \
                           'China_Hefei_reclass_resampled.tif'
        colorado_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data/' \
                              'Colorado_reclass_resampled.tif'
        england_london_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                    'England_London_reclass_resampled.tif'
        england_manchester_sheffield_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                                  'England_Manchester_Sheffield_reclass_resampled.tif'
        france_bordeaux_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                     'France_Bordeaux_reclass_resampled.tif'
        germany_bleicherode_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                         'Germany_Bleicherode_reclass_resampled.tif'
        germany_cologne_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                     'Germany_Cologne_reclass_resampled.tif'
        germany_flensburg_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                       'Germany_Flensburg_reclass_resampled.tif'
        germany_friedewald_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                        'Germany_Friedewald_reclass_resampled.tif'
        germany_hamburg_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                     'Germany_Hamburg_reclass_resampled.tif'
        germany_magdeburg_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                       'Germany_Magdeburg_reclass_resampled.tif'
        greece_alexandreia_palamas_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                                'Greece_Alexandreia_palamas_reclass_resampled.tif'
        greece_patras_katochi_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                           'Greece_Patras_Katochi_reclass_resampled.tif'
        hungary_szeged_romania_timisoara_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                                      'Hungary_Szeged_Romania_Timisoara_reclass_resampled.tif'
        italy_cerignola_campagna_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                              'Italy_Cerignola_Campagna_reclass_resampled.tif'
        italy_lustignano_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                      'Italy_Lustignano_reclass_resampled.tif'
        italy_mazzafarro_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                      'Italy_Mazzafarro_reclass_resampled.tif'
        italy_podelta_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                   'Italy_PoDelta_reclass_resampled.tif'
        italy_rosarno_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                   'Italy_Rosarno_reclass_resampled.tif'
        italy_salerno_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                   'Italy_Salerno_reclass_resampled.tif'
        italy_schiavonea_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                      'Italy_Schiavonea_reclass_resampled.tif'
        lithuania_kaunas_vinius_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                             'Lithuania_Kaunas_Vinius_reclass_resampled.tif'
        netherlands_groningen_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                           'Netherlands_Groningen_reclass_resampled.tif'
        poland_gdansk_gdynia_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                          'Poland_Gdansk_Gdynia_reclass_resampled.tif'
        poland_katowice_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/' \
                                     'Poland_Katowice_reclass_resampled.tif'
        poland_lodz_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/Poland_Lodz_reclass_resampled.tif'
        poland_lubin_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/Poland_Lubin_reclass_resampled.tif'
        spain_murcia_subsidence = '../InSAR_Data/Europe_EGMS/reclass_resample_EGMS/Spain_Murcia_reclass_resampled.tif'
        coastal_subsidence = '../InSAR_Data/Final_subsidence_data/resampled_insar_data' \
                             '/Coastal_subsidence.tif'

        substitute_area_code_on_raster(california_subsidence, california_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/California_area_raster.tif')
        substitute_area_code_on_raster(arizona_subsidence, arizona_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Arizona_area_raster.tif')
        substitute_area_code_on_raster(quetta_subsidence, quetta_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Pakistan_Quetta_area_raster.tif')
        substitute_area_code_on_raster(qazvin_subsidence, qazvin_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Iran_Qazvin_area_raster.tif')
        substitute_area_code_on_raster(hebei_subsidence, hebei_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/China_Hebei_area_raster.tif')
        substitute_area_code_on_raster(hefei_subsidence, hefei_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/China_Hefei_area_raster.tif')
        substitute_area_code_on_raster(colorado_subsidence, colorado_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Colorado_area_raster.tif')
        substitute_area_code_on_raster(england_london_subsidence, england_london_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/England_London_area_raster.tif')
        substitute_area_code_on_raster(england_manchester_sheffield_subsidence, england_manchester_sheffield_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/England_Manchester_Sheffield_area_raster.tif')
        substitute_area_code_on_raster(france_bordeaux_subsidence, france_bordeaux_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/France_Bordeaux_area_raster.tif')
        substitute_area_code_on_raster(germany_bleicherode_subsidence, germany_bleicherode_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Germany_Bleicherode_area_raster.tif')
        substitute_area_code_on_raster(germany_cologne_subsidence, germany_cologne_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Germany_Cologne_area_raster.tif')
        substitute_area_code_on_raster(germany_flensburg_subsidence, germany_flensburg_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Germany_Flensburg_area_raster.tif')
        substitute_area_code_on_raster(germany_friedewald_subsidence, germany_friedewald_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Germany_Friedewald_area_raster.tif')
        substitute_area_code_on_raster(germany_hamburg_subsidence, germany_hamburg_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Germany_Hamburg_area_raster.tif')
        substitute_area_code_on_raster(germany_magdeburg_subsidence, germany_magdeburg_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Germany_Magdeburg_area_raster.tif')
        substitute_area_code_on_raster(greece_alexandreia_palamas_subsidence, greece_alexandreia_palamas_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Greece_Alexandreia_Palamas_area_raster.tif')
        substitute_area_code_on_raster(greece_patras_katochi_subsidence, greece_patras_katochi_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Greece_Patras_Katochi_area_raster.tif')
        substitute_area_code_on_raster(hungary_szeged_romania_timisoara_subsidence,
                                       hungary_szeged_romania_timisoara_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Hungary_Szeged_Romania_Timisoara_area_raster.tif')
        substitute_area_code_on_raster(italy_cerignola_campagna_subsidence, italy_cerignola_campagna_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_Cerignola_Campagna_area_raster.tif')
        substitute_area_code_on_raster(italy_lustignano_subsidence, italy_lustignano_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_Lustignano_area_raster.tif')
        substitute_area_code_on_raster(italy_mazzafarro_subsidence, italy_mazzafarro_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_Mazzafarroo_area_raster.tif')
        substitute_area_code_on_raster(italy_podelta_subsidence, italy_podelta_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_PoDelta_area_raster.tif')
        substitute_area_code_on_raster(italy_rosarno_subsidence, italy_rosarno_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_Rosarno_area_raster.tif')
        substitute_area_code_on_raster(italy_salerno_subsidence, italy_salerno_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_Salerno_area_raster.tif')
        substitute_area_code_on_raster(italy_schiavonea_subsidence, italy_schiavonea_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Italy_Schiavonea_area_raster.tif')
        substitute_area_code_on_raster(lithuania_kaunas_vinius_subsidence, lithuania_kaunas_vinius_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Lithuania_Kaunas_Vinius_area_raster.tif')
        substitute_area_code_on_raster(netherlands_groningen_subsidence, netherlands_groningen_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Netherlands_Groningen_area_raster.tif')
        substitute_area_code_on_raster(poland_gdansk_gdynia_subsidence, poland_gdansk_gdynia_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Poland_Gdansk_Gdynia_area_raster.tif')
        substitute_area_code_on_raster(poland_katowice_subsidence, poland_katowice_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Poland_Katowice_area_raster.tif')
        substitute_area_code_on_raster(poland_lodz_subsidence, poland_lodz_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Poland_Lodz_area_raster.tif')
        substitute_area_code_on_raster(poland_lubin_subsidence, poland_lubin_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/Poland_Lubin_area_raster.tif')
        substitute_area_code_on_raster(spain_murcia_subsidence, spain_murcia_area_code,
                                       '../Model Run/LOO_Test/InSAR_Data/'
                                       'interim_working_dir/spain_murcia_area_raster.tif')

        coastal_raster_area_coded = substitute_area_code_on_raster(coastal_subsidence, coastal_area_code,
                                                                   '../Model Run/LOO_Test/InSAR_Data/'
                                                                   'interim_working_dir/Coastal_raster.tif')

        mosaic_rasters(insar_data_dir, output_dir=insar_data_dir, raster_name='interim_insar_Area_data.tif',
                       ref_raster=refraster, search_by='*area_raster.tif', resolution=0.02)

        # merging georeferenced and insar subsidence data
        georef_arr = read_raster_arr_object(georeferenced_raster_area_coded, get_file=False).flatten()
        insar_arr = read_raster_arr_object(os.path.join(insar_data_dir, 'interim_insar_Area_data.tif'),
                                           get_file=False).flatten()
        interim_subsidence_arr = np.where(insar_arr > 0, insar_arr, georef_arr)

        ref_arr, ref_file = read_raster_arr_object(referenceraster)
        shape = ref_file.shape

        # adding coastal area coded raster
        coastal_arr = read_raster_arr_object(coastal_raster_area_coded, get_file=False).flatten()
        interim_subsidence_arr = interim_subsidence_arr.flatten()
        final_subsidence_arr = \
            np.where(coastal_arr == coastal_area_code, coastal_arr, interim_subsidence_arr).reshape(shape)

        subsidence_data = os.path.join(output_dir, final_subsidence_raster)
        write_raster(final_subsidence_arr, ref_file, ref_file.transform, subsidence_data)

        print('Created final area coded subsidence raster')
        pickle.dump(subsidence_areaname_dict, open(os.path.join(output_dir, 'subsidence_areaname_dict.pkl'),
                                                   mode='wb+'))
        return subsidence_data, subsidence_areaname_dict

    else:
        subsidence_data = os.path.join(output_dir, final_subsidence_raster)
        subsidence_areaname_dict = pickle.load(open(os.path.join(output_dir, 'subsidence_areaname_dict.pkl'),
                                                    mode='rb'))
        return subsidence_data, subsidence_areaname_dict


def create_traintest_df_loo_accuracy(input_raster_dir, subsidence_areacode_dict,
                                     output_dir='../Model Run/LOO_Test/Predictors_csv',
                                     search_by='*.tif', skip_dataframe_creation=False,
                                     exclude_predictors=(
                                             'Alexi ET', 'MODIS ET (kg/m2)', 'Irrigated Area Density (gfsad)',
                                             'GW Irrigation Density giam', 'MODIS PET (kg/m2)', 'Clay content PCA',
                                             'MODIS Land Use', 'Grace', 'Sediment Thickness (m)', 'Clay % 200cm',
                                             'Tmin (°C)', 'RET (mm)', 'Clay Thickness (m)')
                                     ):
    """
    create dataframe from predictor rasters along with area code.

    Parameters:
    input_raster_dir : Input rasters directory.
    subsidence_areacode_dict : subsidence area code dictionary (output from 'combine_georef_insar_subsidence_raster'
                                                                function)
    output_dir : Output directory path.
    search_by : Input raster search criteria. Defaults to '*.tif'.
    skip_predictor_subsidence_compilation : Set to True if want to skip processing.
    exclude_predictors : Tuple of predictors to be excluded from fitted_model training.

    Returns: predictor_df dataframe created from predictor rasters.
    """
    if not skip_dataframe_creation:
        print('Creating area coded predictors csv...')
        predictors = glob(os.path.join(input_raster_dir, search_by))

        predictor_name_dict = {'Alexi_ET': 'Alexi ET', 'Aridity_Index': 'Aridity Index',
                               'Clay_content_PCA': 'Clay content PCA', 'EVI': 'EVI', 'Grace': 'Grace',
                               'Global_Sediment_Thickness': 'Sediment Thickness (m)',
                               'GW_Irrigation_Density_giam': 'GW Irrigation Density giam',
                               'Irrigated_Area_Density_gfsad': 'Irrigated Area Density (gfsad)',
                               'MODIS_ET': 'MODIS ET (kg/m2)', 'MODIS_PET': 'MODIS PET (kg/m2)', 'NDWI': 'NDWI',
                               'Irrigated_Area_Density_meier': 'Normalized Irrigated Area Density',
                               'Population_Density': 'Normalized Population Density', 'SRTM_Slope': '% Slope',
                               'Subsidence': 'Subsidence', 'TRCLM_RET': 'RET (mm)',
                               'TRCLM_precp': 'Precipitation (average monthly) (mm)', 'TRCLM_soil': 'Soil moisture (mm)',
                               'TRCLM_Tmax': 'Tmax (°C)', 'TRCLM_Tmin': 'Tmin (°C)', 'MODIS_Land_Use': 'MODIS Land Use',
                               'TRCLM_ET': 'ET (average monthly) (mm)', 'Clay_Thickness': 'Clay Thickness (m)',
                               'Normalized_clay_indicator': 'Normalized Clay Indicator', 'Clay_200cm': 'Clay % 200cm',
                               'River_gaussian': 'River Gaussian', 'River_distance': 'River Distance (km)',
                               'Confining_layers': 'Confining Layers'}

        predictor_dict = {}
        for predictor in predictors:
            variable_name = predictor[predictor.rfind(os.sep) + 1:predictor.rfind('.')]
            variable_name = predictor_name_dict[variable_name]
            if variable_name not in exclude_predictors:
                raster_arr, file = read_raster_arr_object(predictor, get_file=True)
                raster_arr = raster_arr.flatten()
                predictor_dict[variable_name] = raster_arr

        subsidence_area_arr, subsidence_area_file = \
            read_raster_arr_object('../Model Run/LOO_Test/InSAR_Data/final_subsidence_raster/Subsidence_area_coded.tif')

        predictor_dict['Area_code'] = subsidence_area_arr.flatten()
        predictor_df = pd.DataFrame(predictor_dict)
        predictor_df = predictor_df.dropna(axis=0)
        area_code = predictor_df['Area_code'].tolist()

        area_name_list = list(subsidence_areacode_dict.keys())
        area_code_list = list(subsidence_areacode_dict.values())

        area_name = []
        for code in area_code:
            position = area_code_list.index(code)
            name = area_name_list[position]
            area_name.append(name)

        predictor_df['Area_name'] = area_name

        makedirs([output_dir])
        output_csv = os.path.join(output_dir, 'train_test_area_coded_2013_2019.csv')
        predictor_df.to_csv(output_csv, index=False)

        print('Area coded predictors csv created')
        return predictor_df, output_csv
    else:
        print('Loading area coded predictors csv')
        output_csv = output_dir + '/' + 'train_test_area_coded_2013_2019.csv'
        predictor_df = pd.read_csv(output_csv)
        return predictor_df, output_csv


def train_test_split_loo_accuracy(predictor_csv, loo_test_area_name, pred_attr='Subsidence',
                                  outdir='../Model Run/LOO_Test/Predictors_csv'):
    """
    Create x_train, y_train, x_test, y_test arrays for machine learning fitted_model.

    Parameters:
    predictor_dataframe_csv : Predictor csv filepath.
    loo_test_area_name : Area name which will be used as test data.
    pred_attr : Prediction attribute column name.  Default set to 'Subsidence'.
    outdir : Output directory where train and test csv will be saved.

    Returns : x_train_csv_path, x_train, y_train, x_test, y_test arrays.
    """
    predictor_df = pd.read_csv(predictor_csv)
    predictor_df = reindex_df(predictor_df)
    train_df = predictor_df[predictor_df['Area_name'] != loo_test_area_name]
    x_train_df = train_df.drop(columns=['Area_name', 'Area_code', pred_attr])
    y_train_df = train_df[pred_attr]

    test_df = predictor_df[predictor_df['Area_name'] == loo_test_area_name]
    x_test_df = test_df.drop(columns=['Area_name', 'Area_code', pred_attr])
    y_test_df = test_df[[pred_attr]]

    # x_train_arr = np.array(x_train_df)
    # y_train_arr = np.array(y_train_df)
    # x_test_arr = np.array(x_test_df)
    # y_test_arr = np.array(y_test_df)

    x_train_df_path = os.path.join(outdir, 'x_train_loo_test.csv')
    x_train_df.to_csv(x_train_df_path, index=False)
    y_train_df.to_csv(os.path.join(outdir, 'y_train_loo_test.csv'), index=False)
    x_test_df.to_csv(os.path.join(outdir, 'x_test_loo_test.csv'), index=False)
    y_test_df.to_csv(os.path.join(outdir, 'y_test_loo_test.csv'), index=False)

    return x_train_df_path, x_train_df, y_train_df, x_test_df, y_test_df


def build_ml_classifier(predictor_csv, loo_test_area_name, model='RF', random_state=0,
                        n_estimators=300, max_depth=14, max_features=7, min_samples_leaf=1e-05, min_samples_split=7,
                        class_weight='balanced', bootstrap=True, oob_score=True, n_jobs=-1,
                        accuracy_dir=r'../Model Run/Accuracy_score_loo_test',
                        modeldir='../Model Run/LOO_Test/Model_Loo_test'):
    """
    Build ML 'Random Forest' Classifier.

    Parameters:
    predictor_csv : Predictor csv (with filepath) containing all the predictors.
    loo_test_area_name : Area name which will be used as test data.
    fitted_model : Machine learning fitted_model to run.Can only run random forest 'RF' fitted_model.
    random_state : Seed value. Defaults to 0.
    n_estimators : The number of trees in the forest. Defaults to 500.
    max_depth : Depth of each tree. Default set to 20.
    min_samples_leaf : Minimum number of samples required to be at a leaf node. Defaults to 1.
    min_samples_split : Minimum number of samples required to split an internal node. Defaults to 2.
    max_features : The number of features to consider when looking for the best split. Defaults to 'log2'.
    class_weight : To assign class weight. Default set to 'balanced'.
    bootstrap : Whether bootstrap samples are used when building trees. Defaults to True.
    oob_score : Whether to use out-of-bag samples to estimate the generalization accuracy. Defaults to True.
    n_jobs : The number of jobs to run in parallel. Defaults to -1(using all processors).
    accuracy_dir : Confusion matrix directory. If save=True must need a accuracy_dir.
    modeldir : Model directory to store/load fitted_model. Default is '../Model Run/Model/Model_Loo_test'.

    Returns: rf_classifier (A fitted random forest fitted_model)
    """

    global classifier
    x_train_csv, x_train, y_train, x_test, y_test = \
        train_test_split_loo_accuracy(predictor_csv, loo_test_area_name, pred_attr='Subsidence',
                                      outdir='../Model Run/LOO_test/Predictors_csv')

    makedirs([modeldir])
    model_file = os.path.join(modeldir, model)

    if model == 'RF':
        classifier = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth,
                                            min_samples_leaf=min_samples_leaf, min_samples_split=min_samples_split,
                                            max_features=max_features, max_samples=None, max_leaf_nodes=None,
                                            class_weight=class_weight,
                                            random_state=random_state, bootstrap=bootstrap,
                                            n_jobs=n_jobs, oob_score=oob_score, )

    classifier = classifier.fit(x_train, y_train)
    # y_train_pred = classifier.predict(x_train)
    y_pred = classifier.predict(x_test)
    pickle.dump(classifier, open(model_file, mode='wb+'))

    classification_accuracy(y_test, y_pred, loo_test_area_name, accuracy_dir)

    # # Plotting and saving confusion matrix
    # column_labels = [np.array(['Predicted', 'Predicted', 'Predicted']),
    #                  np.array(['<1cm/yr', '1-5cm/yr', '>5cm/yr'])]
    # index_labels = [np.array(['Actual', 'Actual', 'Actual']),
    #                 np.array(['<1cm/yr', '1-5cm/yr', '>5cm/yr'])]
    #
    # cm_train = confusion_matrix(y_train, y_train_pred)
    # cm_df_train = pd.DataFrame(cm_train, columns=column_labels, index=index_labels)
    # print(cm_df_train)
    #
    # cm_test = confusion_matrix(y_test, y_pred)
    # cm_df_test = pd.DataFrame(cm_test, columns=column_labels, index=index_labels)
    # print(cm_df_test)

    return classifier, loo_test_area_name


def classification_accuracy(y_test, y_pred, loo_test_area_name,
                            accuracy_dir=r'../Model Run/LOO_Test/Accuracy_score'):
    """
    Classification accuracy assessment.

    Parameters:
    y_test : y_test array from train_test_split_loo_accuracy() function.
    y_pred : y_pred data from build_ML_classifier() function.
    classifier : ML classifier from build_ML_classifier() function.
    x_train_csv : path of x train csv from 'train_test_split_loo_accuracy' function.
    loo_test_area_name : test area name for which to create confusion matrix.
    area_index : Index that will help saving accuracy score. (don't need to added manually, fitted_model will take from
                 run_loo_accuracy_test function)
    accuracy_dir : Confusion matrix directory. If save=True must need a accuracy_dir.
    predictor_importance : Default set to True to plot predictor importance plot.
    predictor_imp_keyword : Keyword to save predictor important plot.

    Returns: Confusion matrix, score and predictor importance graph.
    """
    subsidence_training_area_list = [
        'Arizona', 'Australia_Perth', 'Bangladesh_GBDelta', 'California', 'China_Beijing',
        'China_Hebei', 'China_Hefei', 'China_Shanghai', 'China_Tianjin', 'China_Wuhan', 'China_Xian',
        'China_YellowRiverDelta', 'Coastal', 'Colorado', 'Egypt_NileDelta', 'India_Delhi',
        'Indonesia_Bandung', 'Indonesia_Semarang', 'Iran_MarandPlain', 'Iran_Mashhad', 'Iran_Qazvin',
        'Iran_Tehran', 'Iraq_TigrisEuphratesBasin', 'Mexico_MexicoCity', 'Nigeria_Lagos', 'Pakistan_Quetta',
        'Philippines_Manila', 'Taiwan_Yunlin', 'Turkey_Bursa', 'Turkey_Karapinar', 'US_Huston',
        'Vietnam_Hanoi', 'Vietnam_HoChiMinh', 'England_London', 'England_Manchester_Sheffield', 'France_Bordeaux',
        'Germany_Bleicherode', 'Germany_Cologne', 'Germany_Flensburg', 'Germany_Friedewald', 'Germany_Hamburg',
        'Germany_Magdeburg', 'Greece_Alexandreia_Palamas', 'Greece_Patras_Katochi', 'Hungary_Szeged_Romania_Timisoara',
        'Italy_Cerignola_Campagna', 'Italy_Lustignano', 'Italy_Mazzafarro', 'Italy_PoDelta', 'Italy_Rosarno',
        'Italy_Salerno', 'Italy_Schiavonea', 'Lithuania_Kaunas_Vinius', 'Netherlands_Groningen', 'Poland_Gdansk_Gdynia',
        'Poland_Katowice', 'Poland_Lodz', 'Poland_Lubin', 'Spain_Murcia']
    subsidence_training_area_list = sorted(subsidence_training_area_list)

    makedirs([accuracy_dir])

    cm = confusion_matrix(y_test, y_pred)
    cm_df = pd.DataFrame(cm)
    cm_name = loo_test_area_name + '_cmatrix.csv'
    csv = os.path.join(accuracy_dir, cm_name)
    cm_df.to_csv(csv, index=True)

    overall_accuracy = round(accuracy_score(y_test, y_pred), 2)

    # generating classification report
    classification_report_dict = classification_report(y_test, y_pred, output_dict=True)
    del classification_report_dict['accuracy']
    classification_report_df = pd.DataFrame(classification_report_dict)
    classification_report_df.drop(labels='support', inplace=True)
    micro_precision = round(precision_score(y_test, y_pred, average='micro'), 2)
    micro_recall = round(recall_score(y_test, y_pred, average='micro'), 2)
    micro_f1 = round(f1_score(y_test, y_pred, average='micro'), 2)

    classification_report_df['micro avg'] = [micro_precision, micro_recall, micro_f1]
    cols = classification_report_df.columns.tolist()
    if '1.0' not in cols:
        classification_report_df['1.0'] = [np.nan, np.nan, np.nan]
    if '5.0' not in cols:
        classification_report_df['5.0'] = [np.nan, np.nan, np.nan]
    if '10.0' not in cols:
        classification_report_df['10.0'] = [np.nan, np.nan, np.nan]

    classification_report_df = classification_report_df[['1.0', '5.0', '10.0', 'micro avg', 'macro avg',
                                                         'weighted avg']]
    classification_report_df.rename(columns={'1.0': '<1cm/yr', '5.0': '1-5cm/yr', '10.0': '>5cm/yr'}, inplace=True)
    classification_report_df = classification_report_df[classification_report_df.columns].round(2)
    classification_report_csv_name = accuracy_dir + '/' + loo_test_area_name + '_classification_report.csv'
    classification_report_df.to_csv(classification_report_csv_name)

    print('Accuracy Score for {} : {}'.format(loo_test_area_name, overall_accuracy))
    path = accuracy_dir + '/' + 'Accuracy_Reports_Joined' + '/' + 'Accuracy_scores.txt'
    if loo_test_area_name == subsidence_training_area_list[0]:
        os.remove(path)
        txt_object = open(path, 'w+')
    else:
        txt_object = open(path, 'a')
    txt_object.write('Accuracy Score for {} : {} \n'.format(loo_test_area_name, overall_accuracy))
    txt_object.close()

    return overall_accuracy


# def create_prediction_raster(predictors_dir, fitted_model, yearlist=(2013, 2019), search_by='*.tif',
#                              continent_search_by='*continent.shp',
#                              continent_shapes_dir='../Data/Reference_rasters_shapes/continent_extents',
#                              prediction_raster_dir='../Model Run/LOO_Test/Prediction_rasters',
#                              exclude_columns=(), pred_attr='Subsidence', prediction_raster_keyword='RF',
#                              predictor_csv_exists=False, predict_probability_greater_1cm=False):
#     """
#     Create predicted raster from random forest fitted_model.
#
#     Parameters:
#     predictors_dir : Predictor rasters' directory.
#     fitted_model : A fitted_model obtained from random_forest_classifier function.
#     yearlist : Tuple of years for the prediction. Default set to (2013, 2019).
#     search_by : Predictor rasters search criteria. Defaults to '*.tif'.
#     continent_search_by : Continent shapefile search criteria. Defaults to '*continent.tif'.
#     continent_shapes_dir : Directory path of continent shapefiles.
#     prediction_raster_dir : Output directory of prediction raster.
#     exclude_columns : Predictor rasters' name that will be excluded from the fitted_model. Defaults to ().
#     pred_attr : Variable name which will be predicted. Defaults to 'Subsidence_G5_L5'.
#     prediction_raster_keyword : Keyword added to final prediction raster name.
#     predictor_csv_exists : Set to True if predictor csv for each continent exists. Default set to False to create
#                            create new predictor csv (also needed if predictor combinations are changed).
#     predict_probability_greater_1cm : Set to True if want to create >1cm/yr probability raster. Default set to False.
#
#     Returns: Subsidence prediction raster and
#              Subsidence prediction probability raster (if prediction_probability=True).
#     """
#     global raster_file
#     predictor_rasters = glob(os.path.join(predictors_dir, search_by))
#     continent_shapes = glob(os.path.join(continent_shapes_dir, continent_search_by))
#     drop_columns = list(exclude_columns) + [pred_attr]
#
#     continent_prediction_raster_dir = os.path.join(prediction_raster_dir, 'continent_prediction_rasters_'
#                                                    + str(yearlist[0]) + '_' + str(yearlist[1]))
#     makedirs([prediction_raster_dir])
#     makedirs([continent_prediction_raster_dir])
#
#     predictor_name_dict = {'Alexi_ET': 'Alexi ET', 'Aridity_Index': 'Aridity Index',
#                            'Clay_content_PCA': 'Clay content PCA', 'EVI': 'EVI', 'Grace': 'Grace',
#                            'Global_Sediment_Thickness': 'Sediment Thickness (m)',
#                            'GW_Irrigation_Density_giam': 'GW Irrigation Density giam',
#                            'Irrigated_Area_Density_gfsad': 'Irrigated Area Density (gfsad)',
#                            'MODIS_ET': 'MODIS ET (kg/m2)', 'MODIS_PET': 'MODIS PET (kg/m2)', 'NDWI': 'NDWI',
#                            'Irrigated_Area_Density_meier': 'Normalized Irrigated Area Density',
#                            'Population_Density': 'Normalized Population Density', 'SRTM_Slope': '% Slope',
#                            'Subsidence': 'Subsidence', 'TRCLM_RET': 'RET (mm)',
#                            'TRCLM_precp': 'Precipitation (average monthly) (mm)', 'TRCLM_soil': 'Soil moisture (mm)',
#                            'TRCLM_Tmax': 'Tmax (°C)', 'TRCLM_Tmin': 'Tmin (°C)', 'MODIS_Land_Use': 'MODIS Land Use',
#                            'TRCLM_ET': 'ET (average monthly) (mm)', 'Clay_Thickness': 'Clay Thickness (m)',
#                            'Normalized_clay_indicator': 'Normalized Clay Indicator', 'Clay_200cm': 'Clay % 200cm',
#                            'River_gaussian': 'River Gaussian', 'River_distance': 'River Distance (km)',
#                            'Confining_layers': 'Confining Layers'}
#
#     for continent in continent_shapes:
#         continent_name = continent[continent.rfind(os.sep) + 1:continent.rfind('_')]
#
#         predictor_csv_dir = '../Model Run/LOO_Test/Predictors_csv/continent_csv'
#         makedirs([predictor_csv_dir])
#         predictor_csv_name = continent_name + '_predictors.csv'
#         predictor_csv = os.path.join(predictor_csv_dir, predictor_csv_name)
#
#         nan_pos_dict_name = predictor_csv_dir + '/nanpos_' + continent_name  # name to save nan_position_dict
#         print(nan_pos_dict_name)
#         clipped_dir = '../Model Run/LOO_Test/Predictors_csv/Predictors_2013_2019'
#         makedirs([clipped_dir])
#         clipped_predictor_dir = os.path.join(clipped_dir, continent_name + '_predictors_' + str(yearlist[0]) +
#                                              '_' + str(yearlist[1]))
#         if not predictor_csv_exists:
#             predictor_dict = {}
#             nan_position_dict = {}
#             raster_shape = None
#
#             for predictor in predictor_rasters:
#                 variable_name = predictor[predictor.rfind(os.sep) + 1:predictor.rfind('.')]
#                 variable_name = predictor_name_dict[variable_name]
#
#                 if variable_name not in drop_columns:
#                     raster_arr, raster_file = clip_resample_raster_cutline(predictor, clipped_predictor_dir, continent,
#                                                                            naming_from_both=False,
#                                                                            naming_from_raster=True)
#                     raster_shape = raster_arr.shape
#                     raster_arr = raster_arr.reshape(raster_shape[0] * raster_shape[1])
#                     nan_position_dict[variable_name] = np.isnan(raster_arr)
#                     raster_arr[nan_position_dict[variable_name]] = 0
#                     predictor_dict[variable_name] = raster_arr
#
#             pickle.dump(nan_position_dict, open(nan_pos_dict_name, mode='wb+'))
#
#             predictor_df = pd.DataFrame(predictor_dict)
#             predictor_df = predictor_df.dropna(axis=0)
#             predictor_df.to_csv(predictor_csv, index=False)
#
#         else:
#             predictor_df = pd.read_csv(predictor_csv)
#
#             nan_position_dict = pickle.load(open(nan_pos_dict_name, mode='rb'))
#
#             raster_arr, raster_file = clip_resample_raster_cutline(predictor_rasters[1], clipped_predictor_dir,
#                                                                    continent, naming_from_both=False,
#                                                                    naming_from_raster=True)
#             raster_shape = raster_arr.shape
#
#         x = predictor_df.values
#         y_pred = fitted_model.predict(x)
#         print(y_pred.shape)
#         for nan_pos in nan_position_dict.values():
#             print(nan_pos.shape)
#             y_pred[nan_pos] = raster_file.nodata
#         y_pred_arr = y_pred.reshape(raster_shape)
#
#         prediction_raster_name = continent_name + '_prediction_' + str(yearlist[0]) + '_' + str(yearlist[1]) + '.tif'
#         predicted_raster = os.path.join(continent_prediction_raster_dir, prediction_raster_name)
#         write_raster(raster_arr=y_pred_arr, raster_file=raster_file, transform=raster_file.transform,
#                      outfile_path=predicted_raster)
#         print('Prediction raster created for', continent_name)
#
#         if predict_probability_greater_1cm:
#             y_pred_proba = fitted_model.predict_proba(x)
#             y_pred_proba = y_pred_proba[:, 1] + y_pred_proba[:, 2]
#
#             for nan_pos in nan_position_dict.values():
#                 y_pred_proba[nan_pos] = raster_file.nodata
#             y_pred_proba = y_pred_proba.reshape(raster_shape)
#
#             probability_raster_name = continent_name + '_proba_greater_1cm_' + str(yearlist[0]) + '_' + \
#                                      str(yearlist[1]) + '.tif'
#             probability_raster = os.path.join(continent_prediction_raster_dir, probability_raster_name)
#             write_raster(raster_arr=y_pred_proba, raster_file=raster_file, transform=raster_file.transform,
#                          outfile_path=probability_raster)
#             print('Prediction probability for >1cm created for', continent_name)
#
#     raster_name = prediction_raster_keyword + '_prediction' + '.tif'
#     mosaic_rasters(continent_prediction_raster_dir, prediction_raster_dir, raster_name, search_by='*prediction*.tif')
#     print('Global prediction raster created')
#
#     proba_raster_name = prediction_raster_keyword + '_proba_greater_1cm' + '.tif'
#     mosaic_rasters(continent_prediction_raster_dir, prediction_raster_dir, proba_raster_name, search_by='*proba*.tif')


def create_prediction_raster(predictors_dir, model, yearlist=(2013, 2019), search_by='*.tif',
                             continent_search_by='*continent.shp', predictor_csv_exists=False,
                             continent_shapes_dir='../Data/Reference_rasters_shapes/continent_extents',
                             prediction_raster_dir='../Model Run/LOO_Test/Prediction_rasters',
                             exclude_columns=(), pred_attr='Subsidence',
                             prediction_raster_keyword='rf', predict_probability_greater_1cm=True):
    """
    Create predicted raster from random forest fitted_model.
    Parameters:
    predictors_dir : Predictor rasters' directory.
    fitted_model : A fitted fitted_model obtained from random_forest_classifier function.
    yearlist :Tuple of years for the prediction. Default set to (2013, 2019).
    search_by : Predictor rasters search criteria. Defaults to '*.tif'.
    continent_search_by : Continent shapefile search criteria. Defaults to '*continent.tif'.
    predictor_csv_exists : Set to True if predictor csv already exists. Defaults set to False. Should be False if
                           list of drop columns changes.
    continent_shapes_dir : Directory path of continent shapefiles.
    prediction_raster_dir : Output directory of prediction raster.
    exclude_columns : Predictor rasters' name that will be excluded from the fitted_model. Defaults to ().
    pred_attr : Variable name which will be predicted. Defaults to 'Subsidence_G5_L5'.
    prediction_raster_keyword : Keyword added to final prediction raster name.
    predict_probability_greater_1cm : Set to False if probability of prediction of each classes (<1cm, 1-5cm, >5cm)
                                      is required. Default set to True to predict probability of prediction for >1cm.
    Returns: Subsidence prediction raster and
             Subsidence prediction probability raster (if prediction_probability=True).
    """
    global raster_file
    predictor_rasters = glob(os.path.join(predictors_dir, search_by))
    continent_shapes = glob(os.path.join(continent_shapes_dir, continent_search_by))
    drop_columns = list(exclude_columns) + [pred_attr]

    continent_prediction_raster_dir = os.path.join(prediction_raster_dir, 'continent_prediction_rasters_'
                                                   + str(yearlist[0]) + '_' + str(yearlist[1]))
    makedirs([prediction_raster_dir])
    makedirs([continent_prediction_raster_dir])

    predictor_name_dict = {'Alexi_ET': 'Alexi ET', 'Aridity_Index': 'Aridity Index',
                           'Clay_content_PCA': 'Clay content PCA', 'EVI': 'EVI', 'Grace': 'Grace',
                           'Global_Sediment_Thickness': 'Sediment Thickness (m)',
                           'GW_Irrigation_Density_giam': 'GW Irrigation Density giam',
                           'Irrigated_Area_Density_gfsad': 'Irrigated Area Density (gfsad)',
                           'MODIS_ET': 'MODIS ET (kg/m2)', 'MODIS_PET': 'MODIS PET (kg/m2)', 'NDWI': 'NDWI',
                           'Irrigated_Area_Density_meier': 'Normalized Irrigated Area Density',
                           'Population_Density': 'Normalized Population Density', 'SRTM_Slope': '% Slope',
                           'Subsidence': 'Subsidence', 'TRCLM_RET': 'RET (mm)',
                           'TRCLM_precp': 'Precipitation (average monthly) (mm)', 'TRCLM_soil': 'Soil moisture (mm)',
                           'TRCLM_Tmax': 'Tmax (°C)', 'TRCLM_Tmin': 'Tmin (°C)', 'MODIS_Land_Use': 'MODIS Land Use',
                           'TRCLM_ET': 'ET (average monthly) (mm)', 'Clay_Thickness': 'Clay Thickness (m)',
                           'Normalized_clay_indicator': 'Normalized Clay Indicator', 'Clay_200cm': 'Clay % 200cm',
                           'River_gaussian': 'River Gaussian', 'River_distance': 'River Distance (km)',
                           'Confining_layers': 'Confining Layers'}

    for continent in continent_shapes:
        continent_name = continent[continent.rfind(os.sep) + 1:continent.rfind('_')]

        predictor_csv_dir = '../Model Run/LOO_Test/Predictors_csv/continent_csv'
        makedirs([predictor_csv_dir])
        predictor_csv_name = continent_name + '_predictors.csv'
        predictor_csv = os.path.join(predictor_csv_dir, predictor_csv_name)

        nan_pos_dict_name = predictor_csv_dir + '/nanpos_' + continent_name  # name to save nan_position_dict
        clipped_predictor_dir = os.path.join('../Model Run/LOO_Test/Predictors_csv/Predictors_2013_2019',
                                             continent_name + '_predictors_' + str(yearlist[0]) + '_' + \
                                             str(yearlist[1]))

        if not predictor_csv_exists:
            predictor_dict = {}
            nan_position_dict = {}
            raster_shape = None
            for predictor in predictor_rasters:
                variable_name = predictor[predictor.rfind(os.sep) + 1:predictor.rfind('.')]
                variable_name = predictor_name_dict[variable_name]
                if variable_name not in drop_columns:
                    raster_arr, raster_file = clip_resample_raster_cutline(predictor, clipped_predictor_dir, continent,
                                                                           naming_from_both=False,
                                                                           naming_from_raster=True, assigned_name=None)
                    raster_shape = raster_arr.shape
                    raster_arr = raster_arr.reshape(raster_shape[0] * raster_shape[1])
                    nan_position_dict[variable_name] = np.isnan(raster_arr)
                    raster_arr[nan_position_dict[variable_name]] = 0
                    predictor_dict[variable_name] = raster_arr

            pickle.dump(nan_position_dict, open(nan_pos_dict_name, mode='wb+'))

            predictor_df = pd.DataFrame(predictor_dict)
            predictor_df = predictor_df.dropna(axis=0)
            predictor_df = reindex_df(predictor_df)
            # this predictor df consists all input variables including the ones to drop
            predictor_df.to_csv(predictor_csv, index=False)

        else:
            predictor_df = pd.read_csv(predictor_csv)
            predictor_df = reindex_df(predictor_df)

            nan_position_dict = pickle.load(open(nan_pos_dict_name, mode='rb'))

            raster_arr, raster_file = clip_resample_raster_cutline(predictor_rasters[1], clipped_predictor_dir,
                                                                   continent, naming_from_both=False,
                                                                   naming_from_raster=True, assigned_name=None)
            raster_shape = raster_arr.shape

        # Model prediction
        y_pred = model.predict(predictor_df)

        for variable_name, nan_pos in nan_position_dict.items():
            if variable_name not in drop_columns:
                y_pred[nan_pos] = raster_file.nodata

        y_pred_arr = y_pred.reshape(raster_shape)

        prediction_raster_name = continent_name + '_prediction_' + str(yearlist[0]) + '_' + str(yearlist[1]) + '.tif'
        predicted_raster = os.path.join(continent_prediction_raster_dir, prediction_raster_name)
        write_raster(raster_arr=y_pred_arr, raster_file=raster_file, transform=raster_file.transform,
                     outfile_path=predicted_raster)
        print('Prediction raster created for', continent_name)

        if predict_probability_greater_1cm:
            y_pred_proba = model.predict_proba(predictor_df)
            y_pred_proba = y_pred_proba[:, 1] + y_pred_proba[:, 2]

            for variable_name, nan_pos in nan_position_dict.items():
                if variable_name not in drop_columns:
                    y_pred_proba[nan_pos] = raster_file.nodata

            y_pred_proba_arr = y_pred_proba.reshape(raster_shape)

            probability_raster_name = continent_name + '_proba_greater_1cm_' + str(yearlist[0]) + '_' + \
                                      str(yearlist[1]) + '.tif'
            probability_raster = os.path.join(continent_prediction_raster_dir, probability_raster_name)
            write_raster(raster_arr=y_pred_proba_arr, raster_file=raster_file, transform=raster_file.transform,
                         outfile_path=probability_raster)
            print('Prediction probability for >1cm created for', continent_name)

    raster_name = prediction_raster_keyword + '_prediction_' + str(yearlist[0]) + '_' + str(yearlist[1]) + '.tif'
    subsidence_arr, path = mosaic_rasters(continent_prediction_raster_dir, prediction_raster_dir, raster_name,
                                          search_by='*prediction*.tif')

    print('Global prediction raster created')

    if predict_probability_greater_1cm:
        proba_raster_name = prediction_raster_keyword + '_proba_greater_1cm_' + str(yearlist[0]) + '_' + \
                            str(yearlist[1]) + '.tif'
        mosaic_rasters(continent_prediction_raster_dir, prediction_raster_dir, proba_raster_name,
                       search_by='*proba_greater_1cm*.tif')
        print('Global prediction probability raster created')


def run_loo_accuracy_test(predictor_dataframe_csv, exclude_predictors_list, n_estimators=300, max_depth=20,
                          max_features=10, min_samples_leaf=1e-05, min_samples_split=2, class_weight='balanced',
                          predictor_raster_directory='../Model Run/Predictors_2013_2019',
                          skip_create_prediction_raster=False, predictor_csv_exists=False,
                          predict_probability_greater_1cm=False):
    """
    Driver code for running Loo Accuracy Test.

    Parameters:
    predictor_dataframe_csv : filepath of predictor csv.
    exclude_predictors_list : List of predictors to exclude for training the fitted_model.
    n_estimators : The number of trees in the forest. Defaults to 500.
    max_depth : Depth of each tree. Default set to 20.
    min_samples_leaf : Minimum number of samples required to be at a leaf node. Defaults to 1.
    min_samples_split : Minimum number of samples required to split an internal node. Defaults to 2.
    max_features : The number of features to consider when looking for the best split. Defaults to 'log2'.
    class_weight : To assign class weight. Default set to 'balanced'.
    predictor_raster_directory : Original predictor raster directory. Default set to
                                 '../Model Run/Predictors_2013_2019'.
    skip_create_prediction_raster : Set to True if want to skip prediction raster creation.
    predictor_csv_exists : Set to True if predictor csv for each continent exists. Default set to False to create
                           create new predictor csv (also needed if predictor combinations are changed).
    predict_probability_greater_1cm : Set to True if want to create >1cm/yr probability raster. Default set to False.

    Returns : Classification reports and confusion matrix for individual fitted_model training, Overall accuracy result
              for each fitted_model as a single text file, prediction rasters for each fitted_model
              (if skip_create_prediction_raster=False)
    """
    subsidence_training_area_list = [
        'Arizona', 'Australia_Perth', 'Bangladesh_GBDelta', 'California', 'China_Beijing',
        'China_Hebei', 'China_Hefei', 'China_Shanghai', 'China_Tianjin', 'China_Wuhan', 'China_Xian',
        'China_YellowRiverDelta', 'Coastal', 'Colorado', 'Egypt_NileDelta', 'India_Delhi',
        'Indonesia_Bandung', 'Indonesia_Semarang', 'Iran_MarandPlain', 'Iran_Mashhad', 'Iran_Qazvin',
        'Iran_Tehran', 'Iraq_TigrisEuphratesBasin', 'Mexico_MexicoCity', 'Nigeria_Lagos', 'Pakistan_Quetta',
        'Philippines_Manila', 'Taiwan_Yunlin', 'Turkey_Bursa', 'Turkey_Karapinar', 'US_Huston',
        'Vietnam_Hanoi', 'Vietnam_HoChiMinh', 'England_London', 'England_Manchester_Sheffield', 'France_Bordeaux',
        'Germany_Bleicherode', 'Germany_Cologne', 'Germany_Flensburg', 'Germany_Friedewald', 'Germany_Hamburg',
        'Germany_Magdeburg', 'Greece_Alexandreia_Palamas', 'Greece_Patras_Katochi', 'Hungary_Szeged_Romania_Timisoara',
        'Italy_Cerignola_Campagna', 'Italy_Lustignano', 'Italy_Mazzafarro', 'Italy_PoDelta', 'Italy_Rosarno',
        'Italy_Salerno', 'Italy_Schiavonea', 'Lithuania_Kaunas_Vinius', 'Netherlands_Groningen', 'Poland_Gdansk_Gdynia',
        'Poland_Katowice', 'Poland_Lodz', 'Poland_Lubin', 'Spain_Murcia']

    subsidence_training_area_list = sorted(subsidence_training_area_list)

    for area in subsidence_training_area_list:
        print('Running without', area)
        trained_rf, loo_area = build_ml_classifier(predictor_dataframe_csv, area, model='RF', random_state=0,
                                                   n_estimators=n_estimators, max_depth=max_depth,
                                                   max_features=max_features, min_samples_leaf=min_samples_leaf,
                                                   min_samples_split=min_samples_split, class_weight=class_weight,
                                                   bootstrap=True, oob_score=True, n_jobs=-1,
                                                   accuracy_dir=r'../Model Run/LOO_Test/Accuracy_score',
                                                   modeldir='../Model Run/LOO_Test/Model_Loo_test')

        if not skip_create_prediction_raster:
            create_prediction_raster(predictors_dir=predictor_raster_directory, model= trained_rf,
                                     yearlist=[2013, 2019], search_by='*.tif',
                                     continent_search_by='*continent.shp',
                                     continent_shapes_dir='../Data/Reference_rasters_shapes/continent_extents',
                                     prediction_raster_dir='../Model Run/LOO_Test/Prediction_rasters',
                                     exclude_columns=exclude_predictors_list, pred_attr='Subsidence',
                                     prediction_raster_keyword='Trained_without_' + area,
                                     predictor_csv_exists=predictor_csv_exists,
                                     predict_probability_greater_1cm=predict_probability_greater_1cm)


def concat_classification_reports(classification_csv_dir='../Model Run/LOO_Test/Accuracy_score'):
    """
    Merge classification reports from all fitted_model runs.

    Parameters:
    classification_csv_dir : Directory of individual classification reports. Default set to
                             '../Model Run/LOO_Test/Accuracy_score'

    Returns : A joined classification report.
    """
    reports = glob(classification_csv_dir + '/' + '*classification_report*.csv')
    report_df = [pd.read_csv(report) for report in reports]

    area_name = []
    for report in reports:
        area = report[report.rfind(os.sep) + 1:report.find('classification') - 1]
        area_name.append(area)
    merged_reports_df = pd.concat(report_df, keys=area_name, ignore_index=False)
    merged_reports_df = merged_reports_df.reset_index(level=1, drop=True)
    merged_reports_df = merged_reports_df.rename(columns={'Unnamed: 0': 'metrics'})
    merged_reports_df = merged_reports_df[['metrics', '<1cm/yr', '1-5cm/yr', '>5cm/yr', 'micro avg', 'macro avg',
                                           'weighted avg']]
    merged_reports_df.to_csv('../Model Run/LOO_Test/Accuracy_score/Accuracy_Reports_Joined/'
                             'Classification_reports_joined.csv')


def categorize_based_on_probability(run=False):
    """
    Categorizing LOO accuracy results.

    Categorizing Criterion-
            if pixels_greater_40_proba > number_subsidence_pixels:
                accuracy_category = 1, status = 'satisfactory'
            elif 1 <= perc_pixels_greater_40_proba < 20:
                accuracy_category = 2, status = 'acceptable'
            else:
                accuracy_category = 3, status = 'not satisfactory'

            if region_name in region_subsidence_less_1cm:
            accuracy_category = 1, status = 'satisfactory (only <1cm train data)'

    Parameters:
    run : Set to True if want to run this function to assess LOAO test accuracy.

    Returns: A excel file with accuracy category for each region.
    """
    if run:
        polygon_boundary = '../Model Run/LOO_Test/global_georef_InSAR_subsidence_polygons.shp'
        proba_predictions = glob(os.path.join('../Model Run/LOO_Test/Prediction_rasters', '*proba*.tif'))
        region_file_dict = dict()
        subsidence_training_data = '../Model Run/Predictors_2013_2019/Subsidence.tif'

        for file in proba_predictions:
            file_name = file[file.rfind(os.sep) + 1: file.rfind('.')]
            str_list = file_name.split('_')
            filterout_list = ['Trained', 'without', 'proba', 'greater', '1cm', '2013', '2019']
            str_list_filtered = [i for i in str_list if i not in filterout_list]
            area_name = '_'.join(str_list_filtered)
            region_file_dict[area_name] = file

        del region_file_dict['Coastal']

        area_shape_list = [(pol['properties']['Area_Name'], shape(pol['geometry'])) for pol in
                           fiona.open(polygon_boundary)]

        # Some areas where only 1/2/3 pixels are found to be >1 cm/year (compared to all pixeks in that area)
        # are also added to 'region_subsidence_less_1cm' list as judging them by >1 cm/year criteria won't be fair as
        # dominant <1 cm/year subsiding region
        region_subsidence_less_1cm = ['Australia_Perth', 'Colorado', 'Egypt_NileDelta', 'Iraq_TigrisEuphratesBasin',
                                      'Nigeria_Lagos', 'US_Huston', 'Germany_Bleicherode', 'Germany_Flensburg',
                                      'Germany_Friedewald', 'Greece_Patras_Katochi','Italy_Mazzafarro', 'Italy_Rosarno',
                                      'Italy_Salerno', 'Italy_Schiavonea', 'Lithuania_Kaunas_Vinius', 'England_London',
                                      'England_Manchester_Sheffield', 'France_Bordeaux','Germany_Hamburg',
                                      'Germany_Magdeburg', 'Hungary_Szeged_Romania_Timisoara', 'Italy_Lustignano',
                                      'Netherlands_Groningen', 'Poland_Gdansk_Gdynia', 'Poland_Lodz', 'Poland_Lubin']

        for each in area_shape_list:
            region_name, shapely_geom = each
            geom = [mapping(shapely_geom)]
            raster_arr, file = read_raster_arr_object(region_file_dict[region_name])
            proba_prediction_arr, transform = mask(dataset=file, shapes=geom, filled=True, crop=True)
            proba_prediction_arr = proba_prediction_arr.squeeze()

            outdir = '../Model Run/LOO_Test/Accuracy_score/regional_probability_prediction'
            makedirs([outdir])
            saved_raster = os.path.join(outdir, region_name + '.tif')
            write_raster(proba_prediction_arr, file, transform, saved_raster)

            proba_prediction_arr = proba_prediction_arr.flatten()
            total_pixels = np.count_nonzero(np.where(proba_prediction_arr != np.nan, 1, 0))
            pixels_greater_40_proba = np.count_nonzero(np.where(proba_prediction_arr >= 0.40, 1, 0))

            perc_pixels_greater_40_proba = pixels_greater_40_proba * 100 / total_pixels

            subsidence_data, subsidence_file = read_raster_arr_object(subsidence_training_data)
            subsidence_arr, sub_transform = mask(dataset=subsidence_file, shapes=geom, filled=True, crop=True)
            subsidence_arr = subsidence_arr.flatten()
            number_subsidence_pixels = np.count_nonzero(np.where(subsidence_arr > 1, 1, 0))

            if '_' in region_name:
                if 'US' in region_name:
                    country = 'United States'
                else:
                    country = region_name.split('_')[0]
                region = region_name[region_name.find('_') + 1:]

            else:
                country = 'United States'
                region = region_name

            if region_name in region_subsidence_less_1cm:
                accuracy_category = 1
                if 0 <= perc_pixels_greater_40_proba < 15:
                    status = 'satisfactory (only <1cm/year train data)'
                else:
                    status = 'not satisfactory (only <1cm/year train data)'
            else:
                if pixels_greater_40_proba > number_subsidence_pixels:
                    accuracy_category = 1
                    status = 'satisfactory'
                elif 1 <= perc_pixels_greater_40_proba < 20:
                    accuracy_category = 2
                    status = 'acceptable'
                else:
                    accuracy_category = 3
                    status = 'not satisfactory'

            region_file_dict[region_name] = country, region, accuracy_category, status, perc_pixels_greater_40_proba

        country_list = []
        region_list = []
        accuracy_category = []
        status = []
        perc_pixels_greater_40_proba = []

        for i, j in region_file_dict.items():
            country_list.append(j[0])
            region_list.append(j[1])
            accuracy_category.append(j[2])
            status.append(j[3])
            perc_pixels_greater_40_proba.append(j[4])

        loo_accuracy_df = pd.DataFrame(list(zip(country_list, region_list, accuracy_category, status,
                                                perc_pixels_greater_40_proba)),
                                       columns=['Country', 'Region', 'Accuracy Category', 'Accuracy Status',
                                                '% pixels > 40% probability'])
        loo_accuracy_df.to_excel('../Model Run/LOO_Test/LOO_accuracy_stat.xlsx')


def run_loao_test_models(run_loao_test=True, subsidence_data_already_prepared=False, skip_polygon_processing=False,
                         skip_dataframe_creation=False, exclude_predictors=(), predictor_csv_exists=False,
                         skip_create_prediction_raster=False):
    """
    Runs LOAO test models.

    Parameters:
    run_loao_test : Default set to True to run Leave-One-Area-Out (LOAO) test models.
    subsidence_data_already_prepared : Default set to False to prepare subsidence dataset.
    skip_polygon_processing :Set to True to georeferenced polygon merging during preparing subsidence dataset.
                             Default set to False.
    skip_dataframe_creation : Set to True if want to skip train-test dataset creation. Default set to False.
    exclude_predictors : Tuple of predictor names to exclude.
    predictor_csv_exists : Set to False if any change is made in predictor combination. At least need to be False
                           during running one for first regions. Then the code can be stopped and run again setting this
                           parameter as True to save model running time significantly.
    skip_create_prediction_raster : Set to True if want to skip prediction raster creation.

    Returns: Prediction rasters and accuracy results for all model runs.
    """
    if run_loao_test:
        subsidence_raster, areaname_dict = \
            combine_georef_insar_subsidence_raster(already_prepared=subsidence_data_already_prepared,  # #
                                                   skip_polygon_processing=skip_polygon_processing)  # #

        predictor_raster_dir = '../Model Run/Predictors_2013_2019'
        exclude_predictors = list(exclude_predictors)

        df, predictor_csv = \
            create_traintest_df_loo_accuracy(input_raster_dir=predictor_raster_dir,
                                             subsidence_areacode_dict=areaname_dict,
                                             skip_dataframe_creation=skip_dataframe_creation,
                                             exclude_predictors=exclude_predictors)

        run_loo_accuracy_test(predictor_dataframe_csv=predictor_csv, exclude_predictors_list=exclude_predictors,
                              n_estimators=300, max_depth=14, max_features=7, min_samples_leaf=1e-05,
                              min_samples_split=7, class_weight='balanced',
                              predictor_raster_directory='../Model Run/Predictors_2013_2019',
                              skip_create_prediction_raster=skip_create_prediction_raster,  # #
                              predictor_csv_exists=predictor_csv_exists,  # #
                              predict_probability_greater_1cm=True)  # #

        concat_classification_reports(classification_csv_dir='../Model Run/LOO_Test/Accuracy_score')


# LOAO Accuracy Test Run
exclude_predictor = ('Alexi ET', 'MODIS ET (kg/m2)', 'Irrigated Area Density (gfsad)',
                     'GW Irrigation Density giam', 'MODIS PET (kg/m2)', 'Clay content PCA',
                     'MODIS Land Use', 'Grace', 'Sediment Thickness (m)', 'Clay % 200cm',
                     'Tmin (°C)', 'RET (mm)', 'Clay Thickness (m)')

# Set random forest parameters manually in the function from main model hyperparameter tuning.
# Didn't add in the function arguments for maintaining simplicity.
run_loao_test_models(run_loao_test=False,  # Set to False to skip loao test run
                     # and only to run categorize_based_on_probability()
                     exclude_predictors=exclude_predictor,
                     subsidence_data_already_prepared=True,  # #
                     skip_polygon_processing=True,  # #
                     skip_dataframe_creation=True,  # #
                     predictor_csv_exists=True,  # #
                     skip_create_prediction_raster=True)  # #

# Categorizing LOAO Test Results
categorize_based_on_probability(run=True)
