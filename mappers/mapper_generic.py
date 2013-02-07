# Name:         mapper_generic.py
# Purpose:      Generic Mapper for L3/L4 satellite or modeling data
# Authors:      Asuka Yamakava, Anton Korosov, Morten Wergeland Hansen
# Licence:      This file is part of NANSAT. You can redistribute it or modify
#               under the terms of GNU General Public License, v.3
#               http://www.gnu.org/licenses/gpl-3.0.html

from vrt import *
from nansat_tools import Node, latlongSRS
import numpy as np

class Mapper(VRT):
    def __init__(self, fileName, gdalDataset, gdalMetadata, logLevel=30):

        # Remove 'NC_GLOBAL#GDAL_' from keys in gdalDataset
        tmp = {}
        geoMetadata = {}
        for key in gdalMetadata.keys():
            try:
                tmp[key.split('NC_GLOBAL#GDAL_')[1]] = gdalMetadata[key]
                if 'NANSAT' in key:
                    geoMetadata[key.split('NC_GLOBAL#GDAL_NANSAT_')[1]] = gdalMetadata[key]
                    val = tmp.pop(key.split('NC_GLOBAL#GDAL_')[1])
            except:
                continue

        gdalMetadata = tmp
        
        rmMetadatas = ['NETCDF_VARNAME', '_FillValue', '_Unsigned', 'ScaleRatio', 'ScaleOffset', 'dods_variable']

        # Get file names from dataset or subdataset
        subDatasets = gdalDataset.GetSubDatasets()
        if len(subDatasets) == 0:
            fileNames = [fileName]
        else:
            fileNames = [f[0] for f in subDatasets]

        # add bands with metadata and corresponding values to the empty VRT
        metaDict = []
        geoFileDict = {}
        xDatasetSource = ''
        yDatasetSource = ''
        firstXSize = 0
        firstYSize = 0
        for i, fileName in enumerate(fileNames):
            subDataset = gdal.Open(fileName)
            # choose the first dataset whith grid
            if (firstXSize == 0 and firstYSize == 0 and
                    subDataset.RasterXSize > 1 and subDataset.RasterYSize > 1):
                firstXSize = subDataset.RasterXSize
                firstYSize = subDataset.RasterYSize
                firstSubDataset = subDataset
                # get projection from the first subDataset
                projection = firstSubDataset.GetProjection()

            # take bands whose sizes are same as the first band.
            if (subDataset.RasterXSize == firstXSize and
                        subDataset.RasterYSize == firstYSize):
                if 'GEOLOCATION_X_DATASET' in fileName or 'longitude' in fileName:
                    xDatasetSource = fileName
                elif 'GEOLOCATION_Y_DATASET' in fileName or 'latitude' in fileName:
                    yDatasetSource = fileName
                else:
                    for iBand in range(subDataset.RasterCount):
                        subBand = subDataset.GetRasterBand(iBand+1)
                        bandMetadata = subBand.GetMetadata_Dict()
                        if 'PixelFunctionType' in bandMetadata:
                            bandMetadata.pop('PixelFunctionType')
                        sourceBands = iBand + 1
                        #sourceBands = i*subDataset.RasterCount + iBand + 1

                        # generate src metadata
                        src = {'SourceFilename': fileName, 'SourceBand': sourceBands}
                        # set scale ratio and scale offset
                        scaleRatio = bandMetadata.get('ScaleRatio',
                                     bandMetadata.get('scale',
                                     bandMetadata.get('scale_factor', '')))
                        if len(scaleRatio) > 0:
                            src['ScaleRatio'] = scaleRatio
                        scaleOffset = bandMetadata.get('ScaleOffset',
                                      bandMetadata.get('offset',
                                      bandMetadata.get('add_offset', '')))
                        if len(scaleOffset) > 0:
                            src['ScaleOffset'] = scaleOffset
                        # sate DataType
                        src['DataType'] = subBand.DataType
                        
                        # generate dst metadata
                        # get all metadata from input band
                        dst = bandMetadata
                        # set wkv and bandname
                        dst['wkv'] = bandMetadata.get('standard_name', '')
                        bandName = bandMetadata.get('NETCDF_VARNAME', '') # could we also use bandMetadata.get('name')?
                        if len(bandName) == 0:
                            bandName = bandMetadata.get('dods_variable', '')
                        if len(bandName) > 0:
                            dst['name'] = bandName

                        # remove non-necessary metadata from dst
                        for rmMetadata in rmMetadatas:
                            if rmMetadata in dst:
                                dst.pop(rmMetadata)

                        # append band with src and dst dictionaries
                        metaDict.append({'src': src, 'dst': dst})

        # create empty VRT dataset with geolocation only
        VRT.__init__(self, firstSubDataset, srcMetadata=gdalMetadata)

        # add bands with metadata and corresponding values to the empty VRT
        self._create_bands(metaDict)

        if len(projection) == 0:
            # projection was not set automatically
            # get projection from GCPProjection
            projection = geoMetadata.get('GCPProjection', '')
        if len(projection) == 0:
            # no projection was found in dataset or metadata:
            # generate WGS84 by default
            projection = latlongSRS.ExportToWkt()
        # set projection
        self.dataset.SetProjection(self.repare_projection(projection))

        # check if GCPs were added from input dataset
        gcpCount = firstSubDataset.GetGCPCount()
        if gcpCount == 0:
            # if no GCPs in input dataset: try to add GCPs from metadata
            gcpCount = self.add_gcps_from_metadata(geoMetadata)

        # Find proper bands and insert GEOLOCATION ARRAY into dataset
        if len(xDatasetSource) > 0 and len(yDatasetSource) > 0:
            self.add_geolocationArray(GeolocationArray(xDatasetSource, yDatasetSource))
        elif gcpCount == 0:
            # if no GCPs found and not GEOLOCATION ARRAY set: 
            #   Set Nansat Geotransform if it is not set automatically
            geoTransform = self.dataset.GetGeoTransform()
            if len(geoTransform) == 0:
                geoTransformStr = geoMetadata.get('GeoTransform', '(0|1|0|0|0|0|1)')
                geoTransform = eval(geoTransformStr.replace('|', ','))
                self.dataset.SetGeoTransform(geoTransform)

    def repare_projection(self, projection):
        '''Replace odd symbols in projection string '|' => ','; '&' => '"' '''
        return projection.replace("|",",").replace("&",'"')

    def add_gcps_from_metadata(self, geoMetadata):
        '''Get GCPs from strings in metadata and insert in dataset'''
        gcpNames = ['GCPPixel', 'GCPLine', 'GCPX', 'GCPY']
        gcpAllValues = []
        # for all gcp coordinates
        for i, gcpName in enumerate(gcpNames):
            # scan throught metadata and find how many lines with each GCP
            gcpLineCount = 0
            for metaDataItem in geoMetadata:
                if gcpName in metaDataItem:
                    gcpLineCount += 1
            # concat all lines
            gcpString = ''
            for n in range(0, gcpLineCount):
                gcpLineName = '%s_%03d' % (gcpName, n)
                gcpString += geoMetadata[gcpLineName]
            # convert strings to floats
            gcpString = gcpString.strip().replace(' ','')
            gcpValues = []
            # append all gcps from string
            for x in gcpString.split('|'):
                if len(x) > 0:
                    gcpValues.append(float(x))
            #gcpValues = [float(x) for x in gcpString.strip().split('|')]
            gcpAllValues.append(gcpValues)

        # create list of GDAL GCPs
        gcps = []
        for i in range(0, len(gcpAllValues[0])):
            gcps.append(gdal.GCP(gcpAllValues[2][i], gcpAllValues[3][i], 0,
                                 gcpAllValues[0][i], gcpAllValues[1][i]))

        if len(gcps) > 0:
            # get GCP projection and repare
            projection = self.repare_projection(geoMetadata.get('GCPProjection', ''))
            # add GCPs to dataset
            self.dataset.SetGCPs(gcps, projection)
            self._remove_geotransform()

        return len(gcps)
