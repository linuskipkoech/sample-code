# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Program: redistapi.py
 Date: Feb 10, 2021
 Desc: Common utilities for Redistricting

 This module provides a comprehensive API for handling redistricting operations
 including plan management, data export, email notifications, and geospatial
 data processing. It integrates with PostgreSQL/PostGIS databases and GeoServer
 for geospatial operations.

 Key Features:
 - Plan creation and management from templates
 - Geospatial data export (Shapefiles, CSV, PDF)
 - Email notifications with file attachments
 - Multi-threaded TEF (Tabular Equivalent File) generation
 - Integration with Keycloak authentication
 - Support for anonymous and authenticated users

 Version: 1.0
"""
import glob
from django.utils import timezone
from maps.models import Swdblog
from datetime import datetime
from swdbwebgis.settings import MEDIA_ROOT, BASE_DIR, CRCOMM_ADMIN, USRSERVERS,EMAIL
from swdblibs import rlab

import os
import csv
import shutil
import bleach
import traceback
import zipfile
import requests
import json
import subprocess
import concurrent.futures

#email
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import email, smtplib, ssl

import matplotlib as mpl
#from matplotlib.tests.test_category import ax
mpl.use('Agg')

import matplotlib.pyplot as plt
import geopandas as gpd
import contextily as ctx


class RedistApi:
    """
    Main API class for redistricting operations.
    
    This class provides methods for managing redistricting plans, exporting data,
    and handling user interactions. It serves as the primary interface between
    the web application and the underlying geospatial data infrastructure.
    
    Attributes:
        None (stateless class)
    
    Example:
        >>> api = RedistApi()
        >>> plans = api.get_plan_from_own_district(user, 'user_ca_state_assembly', sql_handler, session_id)
    """
    
    def __init__(self):
        """
        Initialize the RedistApi instance.
        
        Note: This is a stateless class, so no instance variables are needed.
        All methods are self-contained and use parameters for data.
        """
        pass

    def get_plan_from_own_district(self, userobj, p_layer, clspgrawsql, p_sessid):
        """
        Retrieve redistricting plans from the user's district table.
        
        This method fetches all plans associated with a specific user and layer type.
        It supports both authenticated users and anonymous COI (Community of Interest)
        users through session-based identification.

        Args:
            userobj (User|None): Django user object. If None, indicates COI admin access.
            p_layer (str): District layer type identifier. Valid values:
                - 'user_board_of_equalization': Board of Equalization districts
                - 'user_ca_state_assembly': California State Assembly districts  
                - 'user_ca_state_senate': California State Senate districts
                - 'user_us_house': US House of Representatives districts
                - 'all': Retrieve data from all district tables (for "My Plan" feature)
            clspgrawsql (object): PostgreSQL raw SQL handler instance
            p_sessid (str): Session identifier for anonymous users

        Returns:
            list: List of dictionaries containing plan data with keys:
                - gid: Unique plan identifier
                - layer_name: Type of district layer
                - plan_name: Name of the redistricting plan
                - locked: Boolean indicating if plan is locked for editing
                - subcode: Submission code
                - map_type: Type of map visualization

        Raises:
            Exception: Logged to Swdblog model for debugging purposes

        Example:
            >>> api = RedistApi()
            >>> plans = api.get_plan_from_own_district(
            ...     user=request.user,
            ...     p_layer='user_ca_state_assembly', 
            ...     clspgrawsql=sql_handler,
            ...     p_sessid='session_123'
            ... )
            >>> print(f"Found {len(plans)} plans")
        """
        layersdata = []
        try:
            # Build base SQL query to retrieve plan information
            # Note: Extended query to include subcode and map_type for enhanced functionality
            sqlstmt = 'SELECT gid,layer_name,plan_name,locked,subcode,map_type FROM users_own_district '
            sufstmt = ' '
            
            # Handle different user types and layer filtering
            if userobj is None:
                # COI anonymous user access
                if p_layer == 'all':
                    # Get all plans for anonymous user (no additional WHERE clause needed)
                    pass            
                else:
                    # Filter by specific layer and session for anonymous user
                    sqlstmt = sqlstmt + 'WHERE layer_name=%s AND sessid=%s' + sufstmt
                    layersdata = clspgrawsql.getRows(sqlstmt, [p_layer, p_sessid], None, True, 'default')
            else:
                # Authenticated user access
                sqlstmt = sqlstmt + 'WHERE users_customuser_id=%s AND sessid=%s' + sufstmt
                if p_layer == 'all':
                    # Get all plans for authenticated user across all layer types
                    layersdata = clspgrawsql.getRows(sqlstmt, [userobj.id, p_sessid], None, True, 'default')
                else:
                    # Filter by specific layer type for authenticated user
                    sqlstmt = sqlstmt + 'AND layer_name=%s' + sufstmt
                    layersdata = clspgrawsql.getRows(sqlstmt, [userobj.id, p_sessid, p_layer], None, True, 'default')

        except Exception as e:
            # Comprehensive error logging for debugging and monitoring
            error_msg = f"Error in get_plan_from_own_district: {str(e.args[0])}"
            print(f'--> {error_msg}')
            print(f'--> Traceback: {traceback.format_exc()}')
            
            # Log error to database for audit trail and debugging
            swdblog = Swdblog(
                logtype='E', 
                logmethod='swdbredist.redisapi.get_plan_from_own_district', 
                created_on=timezone.now(), 
                messages=str(e.args[0])
            )
            swdblog.save()
        finally:
            return layersdata

    def build_url_plan(self, userobj, usrserver, values, clspgrawsql, flag):
        """
        Construct GeoServer WFS URL for retrieving redistricting plan data.
        
        This method builds a properly formatted URL for requesting geospatial data
        from GeoServer based on user permissions and plan specifications. It handles
        both new plan creation and existing district retrieval scenarios.

        Args:
            userobj (User|None): Django user object. None indicates COI admin access.
            usrserver (dict): User server configuration from environment variables.
                Expected structure: {'default': {'URL': 'http://...'}}
            values (dict): Plan configuration containing:
                - layer_name (str): Type of district layer
                - plan_name (str): Name of the redistricting plan
                - sessid (str): Session identifier for anonymous users
            clspgrawsql (object): PostgreSQL raw SQL handler instance
            flag (str): Operation type indicator:
                - 'new_plan': Create URL for new plan with CQL filter
                - 'new_district': Create URL for new district (empty result set)

        Returns:
            str|None: Complete GeoServer WFS URL for data retrieval, or None if error

        Raises:
            Exception: Logged to Swdblog model for debugging purposes

        Example:
            >>> values = {
            ...     'layer_name': 'user_ca_state_assembly',
            ...     'plan_name': 'My Assembly Plan',
            ...     'sessid': 'session_123'
            ... }
            >>> url = api.build_url_plan(
            ...     user=request.user,
            ...     usrserver=USRSERVERS,
            ...     values=values,
            ...     clspgrawsql=sql_handler,
            ...     flag='new_plan'
            ... )
            >>> print(f"GeoServer URL: {url}")
        """
        outret = None
        try:
            # Query to get GeoServer configuration for the specific layer and user
            # This ensures proper access control and layer mapping
            sqlstmt = """SELECT es.workspace, es.url, es.params, l.name 
                FROM users_layers_info ui, external_server es, layers l 
                WHERE ui.external_server_gid = es.gid 
                AND ui.layers_gid = l.gid 
                AND l.name = %s 
                AND ui.is_active = '1' 
                AND es.is_active = '1' 
                AND l.is_active = '1' 
                AND ui.users_customuser_id = %s"""
            
            aret = clspgrawsql.getRows(sqlstmt, [values['layer_name'], userobj.id], None, True, 'default')
            
            # Build base GeoServer URL from configuration
            server_url = usrserver['default']['URL'] + aret[0]['url'] + '?'
            server_url = server_url + aret[0]['params'] + aret[0]['workspace']
            
            # Add layer-specific filtering based on operation type
            if flag == 'new_plan':
                # Create URL for retrieving existing plan data with CQL filter
                server_url = server_url + ':' + aret[0]['name'] + '&CQL_FILTER='
                server_url = server_url + "plan_name IN ('" + values['plan_name'] + "')"
                server_url = server_url + " AND users_customuser_id = '" + str(userobj.id) + "'"
                server_url = server_url + " AND sessid = '" + values['sessid'] + "'"
            elif flag == 'new_district':
                # Create URL for new district (empty result set to start fresh)
                server_url = server_url + ':' + aret[0]['name'] + '&CQL_FILTER=fid IN (0)'
            
            outret = server_url

        except Exception as e:
            print('--> swdbredist.redisapi.build_url_plan : ', str(e.args[0]))
            print('--> swdbredist.redisapi.build_url_plan : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdbredist.redisapi.build_url_plan', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()
        finally:
            return outret

    def make_edit_download(self, clspgrawsql, reqs, userobj):
        """
        Generate downloadable redistricting plan package for users in edit mode.
        
        This method creates a comprehensive export package containing shapefiles,
        CSV data, and documentation for a redistricting plan. It's designed for
        users who want to export their work-in-progress plans before final submission.
        
        The export package includes:
        - Shapefile with district geometries and attributes
        - CSV file with TEF (Tabular Equivalent File) data
        - Text file with plan comments and metadata
        - PDF documentation (if available)
        
        Process Flow:
        1. Extract and validate request parameters
        2. Determine user type (authenticated vs anonymous)
        3. Query database for plan and district data
        4. Create temporary working directory
        5. Generate shapefile via GeoServer WFS request
        6. Create TEF CSV using multi-threaded processing
        7. Generate plan documentation
        8. Package all files for download

        Args:
            clspgrawsql (object): PostgreSQL raw SQL handler instance
            reqs (Request): Django request object containing:
                - sessid (str): Session identifier
                - layer (str): District layer type
                - plan_name (str): Name of the plan to export
                - ulgid (str): User layer group ID (optional)
                - plan_obj (dict): Plan geometry data (optional)
            userobj (User): Django user object

        Returns:
            tuple: (save_folder, p_filename, ldata, ldata) containing:
                - save_folder (str): Path to directory containing export files
                - p_filename (str): Base filename for exported files
                - ldata (list): Plan metadata from database
                - ldata (list): Duplicate of ldata (legacy return format)

        Raises:
            Exception: Logged to Swdblog model for debugging purposes

        Example:
            >>> api = RedistApi()
            >>> folder, filename, plan_data, _ = api.make_edit_download(
            ...     clspgrawsql=sql_handler,
            ...     reqs=request,
            ...     userobj=request.user
            ... )
            >>> print(f"Export created: {folder}/{filename}")
        """
        # Initialize variables for file generation
        p_filename = 'MyCADistrict_' + datetime.today().strftime('%m%d%Y')
        save_file = None
        save_folder = None
        try:
            # Extract and sanitize request parameters
            # Note: Using bleach.clean() for security to prevent XSS attacks
            p_sessid = None
            if('sessid' in reqs.data): 
                p_sessid = bleach.clean(reqs.data.get('sessid'))
            else: 
                p_sessid = None
                
            if('layer' in reqs.data): 
                p_layer = bleach.clean(reqs.data.get('layer')).lower()
            else: 
                p_layer = None
                
            if('plan_name' in reqs.data): 
                p_plan_name = reqs.data.get('plan_name')
            else: 
                p_plan_name = None
                
            if('ulgid' in reqs.data): 
                p_ulgid = bleach.clean(reqs.data.get('ulgid'))
            else: 
                p_ulgid = None
                
            if('plan_obj' in reqs.data): 
                p_gjson = reqs.data.get('plan_obj')
            else: 
                p_gjson = None

            p_gid = None
            dist_fids = []
            
            # Determine save folder based on user type
            # Anonymous users use session ID, authenticated users use user ID
            if userobj.username == 'coi_anonymous' and p_sessid is not None:
                save_folder = MEDIA_ROOT + '/tmp/' + str(p_sessid)
            else:
                save_folder = MEDIA_ROOT + '/tmp/' + str(userobj.id)
                p_sessid = str(userobj.id)

            if p_gjson is not None:
                # Get districts in a plan
                sqlstmt = 'SELECT * FROM ' + 'tmp_' + p_layer + \
                    ' WHERE users_customuser_id=%s AND sessid = %s \
                    AND plan_name=%s'
                ldata = clspgrawsql.getRows(sqlstmt,
                     [userobj.id,str(p_sessid),p_plan_name], None, True, 'default')

                if len(ldata) > 0:
                    p_gid = '0'
                    p_filename = 'MyCADistrict_' + self.get_layer_type_name(p_layer)
                    p_filename = p_filename + datetime.today().strftime('%m%d%Y') + p_gid
                    save_file = save_folder + '/' + p_filename

                if save_folder is not None and os.path.isdir(save_folder):
                    shutil.rmtree(save_folder)

                os.mkdir(save_folder)

                # Get districts in a plan
                sqlstmt = 'SELECT * FROM tmp_' + p_layer  + \
                   ' WHERE users_customuser_id = %s AND sessid = %s \
                    AND plan_name=%s '
                recs = clspgrawsql.getRows(sqlstmt,
                    [userobj.id,str(p_sessid),p_plan_name], None, True, 'default')

                if len(recs) > 0:
                    for idx in recs: dist_fids.append(idx['fid'])

                # Got district fids for a plan from geoserver
                if len(dist_fids) > 0:
                    # Write text file
                    with open(save_file + '.txt', mode='w', encoding='utf-8') as fd:
                        teftxt = csv.writer(fd)
                        teftxt.writerow(['subcode','fid','districtname','comments'])
                        teftxt.writerow([ldata[0]['subcode'],'General Plan Comment',ldata[0]['general']])
                        for idx in recs:
                            teftxt.writerow([idx['subcode'],idx['fid'],idx['districtname'],idx['comments']])

                    table_convert = {'user_board_of_equalization': 'tmp_user_board_of_equalization',
                                 'user_ca_state_assembly': 'tmp_user_ca_state_assembly',
                                 'user_ca_state_senate': 'tmp_user_ca_state_senate',
                                 'user_us_house': 'tmp_user_us_house'}

                    # Write shapefile
                    sqlstmt = "SELECT workspace, url, params \
                        FROM external_server WHERE is_active = '1' AND workspace = %s"
                    edata = clspgrawsql.getRows(sqlstmt, ['swdbdist_ca'], None, True, 'default')
                    edata = edata[0]
                    dval = dict(x.split('=') for x in edata['params'].split('&'))
                    dval['outputFormat'] = 'shape-zip'
                    dval['typeName'] = edata['workspace'] + ':' + table_convert[p_layer]
                    dval['propertyName'] = 'fid,users_customuser_id,sessid,cit_19,nh_wht_cit_19,nh_othmr_cit_19,hsp_cit_19,cvap_19,nh_wht_cvap_19,nh_othmr_cvap_19,hsp_cvap_19,doj_nh_ind_cit_19,doj_nh_ind_cvap_19,doj_nh_blk_cit_19,doj_nh_blk_cvap_19,doj_nh_asn_cit_19,doj_nh_asn_cvap_19,population,hispanic_origin,nh_wht,population_18,h18_pop,nh18_wht,doj_nh_blk,doj_nh_ind,doj_nh_asn,doj_nh_hwn,doj_nh_oth,doj_nh_othmr,doj_nh18_blk,doj_nh18_ind,doj_nh18_asn,doj_nh18_hwn,doj_nh18_oth,doj_nh18_othmr,percideal,percentlatinopop,percentwhitepop,percentblack,percentasian,percentmmr,vapercentlatino,vapercentwhite,vapercentblack,vapercentasian,vapercentmmr,cvappercentlatino,cvappercentwhite,cvappercentblack,cvappercentasian,cvappercentmmrnh,district_type,districtname,plan_name,stylecolor,map_type,locked,comments,general,created_at,updated_at,submitted_at,contiguity,popdeviation,subassign,subemail,subphone,suback,sendemail,geom'
                    dval['format_options'] = 'filename:' + p_filename + '.zip;CHARSET:UTF-8'
                    dreq = ''

                    # Convert district fids into string with comma
                    alist = [str(element) for element in dist_fids]
                    jfids = ",".join(alist)
    
                    for k, v in dval.items(): dreq = dreq + k + '=' + v + '&'
                    server_url = USRSERVERS['default']['URL'] + edata['url'] + \
                        '?' + dreq + 'CQL_FILTER=fid IN (' + str(jfids) + ')'

                    # Get file from geoserver
                    r = requests.get(server_url, stream = True)
                    with open(save_file, 'wb') as fd:
                        for chunk in r.iter_content(chunk_size=1024):
                            fd.write(chunk)

                    if os.path.isfile(save_file):
                        #remove the geosrver texts files
                        with zipfile.ZipFile(save_file,"r") as zip_ref:
                            zip_ref.extractall(save_folder)
    
                        # self.create_png_from_shp(shpfile[0],pngfile)
                        if os.path.isfile(save_folder + '/wfsrequest.txt'):
                            os.remove(save_folder + '/wfsrequest.txt')

                    # Remove file from geoserver
                    if os.path.isfile(save_file): os.remove(save_file)

                    # Rename shapefile to subcode
                    for xidx in os.listdir(save_folder):
                        ex = os.path.splitext(xidx)[1]
                        os.rename(save_folder + '/' + xidx, save_folder + '/' + p_filename + ex)

                # Add UTF-8
                with open(save_file + '.cst', 'w') as file1:
                    file1.write('UTF-8')

                # Make TEFClean data
                sqlstmt = "DELETE FROM users_district_tef \
                        WHERE users_customuser_id = %s \
                        AND layer_name = %s AND plan_name=%s AND sessid=%s "
                xret = clspgrawsql.getExecute(sqlstmt, [userobj.id,
                        p_layer, p_plan_name, str(p_sessid)], dbname='default', returnid=None)

                """
                # Create tef from userid, layer and plan_name
                sqlstmt = "INSERT INTO users_district_tef (users_customuser_id, \
                    layer_name, plan_name, sessid, district_fid, centroids_fid) \
                    SELECT distinct %s,%s,%s,%s, u.fid ufid, c.fid cfid \
                    FROM tmp_" + p_layer + " u, coi_centroids c \
                    WHERE (u.geom && c.geom) AND ST_Contains(u.geom, c.geom) \
                    AND u.users_customuser_id = %s AND u.plan_name = %s \
                    AND u.sessid = %s "
                xret = clspgrawsql.getExecute(sqlstmt, [userobj.id,
                    p_layer, p_plan_name, str(p_sessid), userobj.id, p_plan_name, str(p_sessid)],
                    dbname='default', returnid=None)

                # Grab geoid20 from users_district_tef
                sqlstmt = "SELECT distinct c.geoid20, l.districtname \
                    FROM coi_centroids c, users_district_tef t, " + \
                    'tmp_' + p_layer + " l \
                    WHERE c.fid = t.centroids_fid \
                    AND t.users_customuser_id = %s AND t.sessid = %s \
                    AND t.layer_name = %s AND t.plan_name = %s \
                    AND t.district_fid = l.fid \
                    ORDER by l.districtname, c.geoid20"
                tef_csv_data = list(clspgrawsql.getRows(sqlstmt,
                    [userobj.id, str(p_sessid), p_layer, p_plan_name], 'all', False, 'default'))
                """

                sqlstmt = "SELECT distinct districtname \
                    FROM tmp_" + p_layer + " \
                    WHERE users_customuser_id = %s \
                    AND plan_name = %s AND sessid = %s ORDER BY districtname"
                recs_district = list(clspgrawsql.getRows(sqlstmt, [userobj.id, 
                    p_plan_name, str(p_sessid)], 'all', False, 'default'))

                # Multi-threaded TEF (Tabular Equivalent File) generation
                # This improves performance by processing districts in parallel
                # Each thread handles one district's TEF data generation
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    futures = []
                    for rec in range(len(recs_district)):
                        # Prepare arguments for each thread
                        argobjs = {
                            'p_layer': p_layer, 
                            'userid': userobj.id, 
                            'plan_name': p_plan_name, 
                            'sessid': str(p_sessid),
                            'save_folder': save_folder, 
                            'p_filename': p_filename,
                            'districtname': recs_district[rec], 
                            'clspgrawsql': clspgrawsql,
                            'idx': rec
                        }
                        # Submit each district's TEF generation to a separate thread
                        futures.append(executor.submit(self.thread_make_tef, argobjs))

                    # Note: Results are collected in the main thread below
                    # Uncomment the following lines for debugging thread results:
                    # for future in concurrent.futures.as_completed(futures):
                    #     print('--> Thread result: ', future.result())

                save_file = save_folder + '/' + p_filename
                with open(save_file + '.csv', mode='w', encoding='utf-8') as fd:
                    fd.write('geoid20,districtname\n')
                    for rec in range(len(recs_district)):
                        with open(save_folder + '/' + str(rec) + '.csv') as fr:
                            contents = fr.readlines()
                        fd.writelines(contents)
                        os.remove(save_folder + '/' + str(rec) + '.csv')

                """
                sqlstmt = "SELECT c.geoid20, u.districtname \
                    FROM tmp_" + p_layer + " u, coi_centroids c \
                    WHERE (u.geom && c.geom) AND ST_Contains(u.geom, c.geom) \
                    AND u.users_customuser_id = %s \
                    AND u.plan_name = %s AND u.sessid = %s"
                #ORDER by u.districtname, c.geoid20"
                tef_csv_data = list(clspgrawsql.getRows(sqlstmt, [userobj.id, 
                    p_plan_name, str(p_sessid)], 'all', False, 'default'))
            
                save_file = save_folder + '/' + p_filename
                with open(save_file + '.csv', mode='w', encoding='utf-8') as fd:
                    teftxt = csv.writer(fd)
                    teftxt.writerow(['geoid20','districtname'])
                    teftxt.writerows(tef_csv_data)
                """

                # Make TEFClean data
                sqlstmt = "DELETE FROM users_district_tef \
                        WHERE users_customuser_id = %s \
                        AND layer_name = %s AND plan_name=%s AND sessid=%s "
                xret = clspgrawsql.getExecute(sqlstmt, [userobj.id,
                        p_layer, p_plan_name, str(p_sessid)], dbname='default', returnid=None)

                # ldata[0] contains data for pdf front page from users_own_district
                myldata = dict(ldata[0])
                myldata['layer_name'] = p_layer
                rlab.district_front_page(save_folder, p_filename, myldata, ldata)

        except Exception as e:
            print('--> swdbredist.redisapi.make_edit_download : ', str(e.args[0]))
            print('--> swdbredist.redisapi.make_edit_download : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdbredis.redistapi.make_edit_download', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()
        finally:
            return save_folder, p_filename, ldata, ldata
            #return save_folder, p_filename, ldata, recs

    def thread_make_tef(self, argobjs):
        """
        Thread worker function for generating TEF (Tabular Equivalent File) data.
        
        This method runs in a separate thread to generate TEF CSV data for a single
        district. It performs spatial intersection queries to determine which census
        blocks (identified by geoid20) fall within each district boundary.
        
        The TEF is a critical component for redistricting analysis as it provides
        the mapping between census geography and district assignments, enabling
        demographic analysis and compliance checking.

        Args:
            argobjs (dict): Dictionary containing thread parameters:
                - p_layer (str): District layer type
                - userid (int): User ID
                - plan_name (str): Plan name
                - sessid (str): Session ID
                - save_folder (str): Directory to save CSV file
                - p_filename (str): Base filename
                - districtname (str): Name of the district to process
                - clspgrawsql (object): Database handler
                - idx (int): Thread index for unique file naming

        Returns:
            None: Creates CSV file in save_folder with format: {idx}.csv

        Raises:
            Exception: Logged for debugging (commented out to avoid thread conflicts)
        """
        try:
            # Extract parameters from argument dictionary
            p_layer = argobjs['p_layer']
            p_userid = argobjs['userid']
            p_plan_name = argobjs['plan_name']
            p_sessid = argobjs['sessid']
            save_folder = argobjs['save_folder']
            p_filename = argobjs['p_filename']
            p_district = argobjs['districtname']
            clspgrawsql = argobjs['clspgrawsql']
            p_idx = argobjs['idx']

            # Spatial intersection query to find census blocks within district
            # Uses PostGIS spatial functions for efficient geometric operations:
            # - && operator: bounding box intersection (fast pre-filter)
            # - ST_Contains: precise geometric containment check
            sqlstmt = """SELECT c.geoid20, u.districtname 
                    FROM tmp_""" + p_layer + """ u, coi_centroids c 
                    WHERE (u.geom && c.geom) AND ST_Contains(u.geom, c.geom) 
                    AND u.users_customuser_id = %s 
                    AND u.plan_name = %s AND u.sessid = %s 
                    AND u.districtname = %s 
                    ORDER by u.districtname, c.geoid20"""
            
            tef_csv_data = list(clspgrawsql.getRows(sqlstmt, [p_userid, 
                p_plan_name, p_sessid, p_district], 'all', False, 'default'))

            # Write TEF data to CSV file with thread-specific naming
            save_file = save_folder + '/' + str(p_idx)
            with open(save_file + '.csv', mode='w', encoding='utf-8') as fd:
                teftxt = csv.writer(fd)
                # Note: Header row is written in main thread to avoid duplication
                # teftxt.writerow(['geoid20','districtname'])
                teftxt.writerows(tef_csv_data)

        except Exception as e:
            print('--> swdbredist.redisapi.thread_make_tef : ', str(e.args[0]))
            print('--> swdbredist.redisapi.thread_make_tef : ', traceback.format_exc())
            #swdblog = Swdblog(logtype='E', logmethod='swdbredis.redistapi.make_edit_download', created_on=timezone.now(), messages=str(e.args[0]))
            #swdblog.save()

    def getDistrictGeoserver(self, clspgrawsql, reqs, userobj=None, plocked=None):
        """
        Get a COI from Geoserver

        :param userobj: Django user object
        :param userobj: object

        :param clspgrawsql: Postgres raw SQL class
        :param clspgrawsql: obj

        :param reqs: HTTP request object
        :param reqs: obj

        :param p_fid: feature id from p_layer table
        :param p_fid: int
        """
        p_filename = 'MyCADistrict_' + datetime.today().strftime('%m%d%Y')
        try:
            p_sessid = None
            # COI anonymous
            if('sessid' in reqs.data): p_sessid = bleach.clean(reqs.data.get('sessid'))
            else: p_sessid = None
            if('layer' in reqs.data): p_layer = bleach.clean(reqs.data.get('layer')).lower()
            else: p_layer = None
            if('plan_name' in reqs.data): p_plan_name = reqs.data.get('plan_name')
            else: p_plan_name = None
            if('ulgid' in reqs.data): p_ulgid = bleach.clean(reqs.data.get('ulgid'))
            else: p_ulgid = None
            if plocked is None: p_locked = True
            else: p_locked = False

            save_file = None
            save_folder = None
            p_gid = None
            dist_fids = []

            # Make sure that there is a plan
            if userobj is None and p_ulgid is not None:
                sqlstmt = 'SELECT * FROM users_own_district WHERE locked = %s and gid = %s'
                ldata = clspgrawsql.getRows(sqlstmt, [p_locked, p_ulgid], None, True, 'default')
                save_folder = MEDIA_ROOT + '/tmp/crcomm/' + str(p_ulgid)

            elif userobj is not None:

                if userobj.username == 'coi_anonymous' and p_sessid is not None:
                    save_folder = MEDIA_ROOT + '/tmp/' + str(p_sessid)
                else:
                    save_folder = MEDIA_ROOT + '/tmp/' + str(userobj.id)
                    p_sessid = str(userobj.id)

                sqlstmt = 'SELECT * FROM users_own_district \
                    WHERE users_customuser_id=%s AND sessid = %s \
                    AND layer_name=%s AND plan_name=%s AND locked = %s'
                ldata = clspgrawsql.getRows(sqlstmt,
                     [userobj.id,str(p_sessid),p_layer,p_plan_name,p_locked], None, True, 'default')

            # name the file with gid not fid
            if len(ldata) > 0:
                p_gid = str(ldata[0]['gid'])
                p_layer = str(ldata[0]['layer_name'])
                p_plan_name = str(ldata[0]['plan_name'])
                p_filename = 'MyCADistrict_' + self.get_layer_type_name(p_layer)
                
                if ldata[0]['submitted_at'] is not None:
                    p_filename = p_filename + ldata[0]['submitted_at'].strftime('%m%d%Y') + p_gid
                else:
                    p_filename = p_filename + datetime.today().strftime('%m%d%Y') + p_gid

                save_file = save_folder + '/' + p_filename

            # Get districts in a plan
            if userobj is None and p_ulgid is not None:
                sqlstmt = 'SELECT * FROM ' + p_layer  + \
                   ' WHERE users_customuser_id = %s AND sessid = %s \
                    AND plan_name=%s AND locked = %s'
                recs = clspgrawsql.getRows(sqlstmt,
                    [ldata[0]['users_customuser_id'],str(ldata[0]['sessid']),p_plan_name,p_locked],
                    None, True, 'default')

            elif userobj is not None:
                sqlstmt = 'SELECT * FROM ' + p_layer  + \
                   ' WHERE users_customuser_id = %s AND sessid = %s \
                    AND plan_name=%s AND locked = %s'
                recs = clspgrawsql.getRows(sqlstmt,
                    [userobj.id,str(p_sessid),p_plan_name,p_locked], None, True, 'default')

            if len(recs) > 0:
                for idx in recs: dist_fids.append(idx['fid'])

            # Got district fids for a plan from geoserver
            if len(dist_fids) > 0:
                if save_folder is not None and os.path.isdir(save_folder):
                    shutil.rmtree(save_folder)

                os.mkdir(save_folder)
                
                # Write text file
                with open(save_file + '.txt', mode='w', encoding='utf-8') as fd:
                    teftxt = csv.writer(fd)
                    teftxt.writerow(['subcode','fid','districtname','comments','submitted_by'])
                    teftxt.writerow([ldata[0]['subcode'],'General Plan Comment',ldata[0]['general'],ldata[0]['submitted_by']])
                    for idx in recs:
                        teftxt.writerow([idx['subcode'],idx['fid'],idx['districtname'],idx['comments']])

                if p_locked:
                    table_convert = {'user_board_of_equalization': 'submitted_boe',
                                 'user_ca_state_assembly': 'submitted_assembly',
                                 'user_ca_state_senate': 'submitted_senate',
                                 'user_us_house': 'submitted_house'}
                else:
                    table_convert = {'user_board_of_equalization': 'user_board_of_equalization',
                                 'user_ca_state_assembly': 'user_ca_state_assembly',
                                 'user_ca_state_senate': 'user_ca_state_senate',
                                 'user_us_house': 'user_us_house'}

                # Write shapefile
                sqlstmt = "SELECT workspace, url, params \
                    FROM external_server WHERE is_active = '1' AND workspace = %s"
                edata = clspgrawsql.getRows(sqlstmt, ['swdbdist_ca'], None, True, 'default')
                edata = edata[0]
                dval = dict(x.split('=') for x in edata['params'].split('&'))
                dval['outputFormat'] = 'shape-zip'
                dval['typeName'] = edata['workspace'] + ':' + table_convert[p_layer]
                if p_locked:
                    dval['propertyName'] = 'fid,users_customuser_id,sessid,totc,nhwhtc,nhothmrc,hspc,totcv,nhwhtcv,nhothmrcv,hspcv,dojnhindc,dojnhindcv,dojnhblkc,dojnhblkcv,dojnhasnc,dojnhasncv,tot_pop,hsp_pop,nh_whtpop,tot_vap,hsp_vap,nh_whtvap,doj_blkpop,doj_indpop,doj_asnpop,doj_hwnpop,doj_othpop,doj_bmrpop,doj_blkvap,doj_indvap,doj_asnvap,doj_hwnvap,doj_othvap,doj_bmrvap,percideal,perchsppop,percwhtpop,percblkpop,percasnpop,percmmrpop,perchspvap,percwhtvap,percblkvap,percasnvap,percmmrvap,prchspcvap,prcwhtcvap,prcblkcvap,prcasncvap,prcmmrcvap,district_type,districtname,plan_name,stylecolor,map_type,locked,comments,general,created_at,updated_at,submitted_at,contiguity,popdeviation,subassign,subemail,subphone,suback,sendemail,subcode,submitted_by,geom'
                else:
                    dval['propertyName'] = 'fid,users_customuser_id,sessid,cit_19,nh_wht_cit_19,nh_othmr_cit_19,hsp_cit_19,cvap_19,nh_wht_cvap_19,nh_othmr_cvap_19,hsp_cvap_19,doj_nh_ind_cit_19,doj_nh_ind_cvap_19,doj_nh_blk_cit_19,doj_nh_blk_cvap_19,doj_nh_asn_cit_19,doj_nh_asn_cvap_19,population,hispanic_origin,nh_wht,population_18,h18_pop,nh18_wht,doj_nh_blk,doj_nh_ind,doj_nh_asn,doj_nh_hwn,doj_nh_oth,doj_nh_othmr,doj_nh18_blk,doj_nh18_ind,doj_nh18_asn,doj_nh18_hwn,doj_nh18_oth,doj_nh18_othmr,percideal,percentlatinopop,percentwhitepop,percentblack,percentasian,percentmmr,vapercentlatino,vapercentwhite,vapercentblack,vapercentasian,vapercentmmr,cvappercentlatino,cvappercentwhite,cvappercentblack,cvappercentasian,cvappercentmmrnh,district_type,districtname,plan_name,stylecolor,map_type,locked,comments,general,created_at,updated_at,submitted_at,contiguity,popdeviation,subassign,subemail,subphone,suback,sendemail,submitted_by,geom'

                dval['format_options'] = 'filename:' + p_filename + '.zip;CHARSET:UTF-8'
                dreq = ''

                # Convert district fids into string with comma
                alist = [str(element) for element in dist_fids]
                jfids = ",".join(alist)

                for k, v in dval.items(): dreq = dreq + k + '=' + v + '&'
                server_url = USRSERVERS['default']['URL'] + edata['url'] + \
                    '?' + dreq + 'CQL_FILTER=fid IN (' + str(jfids) + ')'

                # Get file from geoserver
                r = requests.get(server_url, stream = True)
                with open(save_file, 'wb') as fd:
                    for chunk in r.iter_content(chunk_size=1024):
                        fd.write(chunk)

                if os.path.isfile(save_file):
                    #remove the geosrver texts files
                    with zipfile.ZipFile(save_file,"r") as zip_ref:
                        zip_ref.extractall(save_folder)

                    # self.create_png_from_shp(shpfile[0],pngfile)
                    if os.path.isfile(save_folder + '/wfsrequest.txt'):
                        os.remove(save_folder + '/wfsrequest.txt')

                # Remove file from geoserver
                if os.path.isfile(save_file): os.remove(save_file)

                # Rename shapefile to subcode
                for xidx in os.listdir(save_folder):
                    ex = os.path.splitext(xidx)[1]
                    os.rename(save_folder + '/' + xidx, save_folder + '/' + p_filename + ex)

        except Exception as e:
            print('--> swdbredist.redisapi.getDistrictGeoserver : ', str(e.args[0]))
            print('--> swdbredist.redisapi.getDistrictGeoserver : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdbredis.redistapi.getDistrictGeoserver', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()
        finally:
            return save_folder, p_filename, ldata, recs

    def make_tefcsv(self, clspgrawsql, reqs, save_folder, p_filename, userobj=None, plocked=None):
        """
        Make TEF for Redistrict
        """
        try:
            if('layer' in reqs.data): p_layer = bleach.clean(reqs.data.get('layer')).lower()
            else: p_layer = None
            if('plan_name' in reqs.data): p_plan_name = reqs.data.get('plan_name')
            else: p_plan_name = None
            if('sessid' in reqs.data): p_sessid = bleach.clean(reqs.data.get('sessid'))
            else: p_sessid = None
            if('ulgid' in reqs.data): p_ulgid = bleach.clean(reqs.data.get('ulgid'))
            else: p_ulgid = None
            if plocked is None: p_locked = True
            else: p_locked = False

            p_gid = None
            user_id = None
            #Get plan from user own district
            if userobj is None and p_ulgid is not None:
                sqlstmt = 'SELECT * FROM users_own_district WHERE locked = %s and gid = %s'
                ldata = clspgrawsql.getRows(sqlstmt, [p_locked,p_ulgid], None, True, 'default')
                p_gid = str(ldata[0]['gid'])
                user_id = ldata[0]['users_customuser_id']
                p_sessid = str(ldata[0]['sessid'])
                p_plan_name = str(ldata[0]['plan_name'])
                p_layer = str(ldata[0]['layer_name'])

            elif userobj is not None:

                user_id = userobj.id
                if userobj.username != 'coi_anonymous': p_sessid = user_id

                sqlstmt = 'SELECT * FROM users_own_district  \
                        WHERE users_customuser_id = %s AND sessid = %s \
                        AND plan_name=%s AND layer_name=%s AND locked = %s'
                ldata = clspgrawsql.getRows(sqlstmt,
                        [user_id,str(p_sessid),p_plan_name,p_layer,p_locked], None, True, 'default')
                p_gid = str(ldata[0]['gid'])

            if p_gid is not None:

                # Clean data
                sqlstmt = "DELETE FROM users_district_tef \
                        WHERE users_customuser_id = %s \
                        AND layer_name = %s AND plan_name=%s AND sessid=%s "
                xret = clspgrawsql.getExecute(sqlstmt, [user_id,
                        p_layer, p_plan_name, str(p_sessid)], dbname='default', returnid=None)

                # Create tef from userid, layer and plan_name
                sqlstmt = "INSERT INTO users_district_tef (users_customuser_id, \
                    layer_name, plan_name, sessid, district_fid, centroids_fid) \
                    SELECT distinct %s,%s,%s,%s, u.fid ufid, c.fid cfid \
                    FROM " + p_layer + " u, coi_centroids c \
                    WHERE ST_Contains(u.geom, c.geom) \
                    AND u.users_customuser_id = %s AND u.plan_name = %s \
                    AND u.sessid = %s "
                xret = clspgrawsql.getExecute(sqlstmt, [user_id,
                    p_layer, p_plan_name, str(p_sessid), user_id, p_plan_name, str(p_sessid)],
                    dbname='default', returnid=None)

                # Grab geoid20 from users_district_tef
                sqlstmt = "SELECT distinct c.geoid20, l.districtname \
                    FROM coi_centroids c, users_district_tef t, " + \
                    p_layer + " l \
                    WHERE c.fid = t.centroids_fid \
                    AND t.users_customuser_id = %s AND t.sessid = %s \
                    AND t.layer_name = %s AND t.plan_name = %s \
                    AND t.district_fid = l.fid \
                    ORDER by l.districtname, c.geoid20"
                tef_csv_data = list(clspgrawsql.getRows(sqlstmt,
                    [user_id, str(p_sessid), p_layer, p_plan_name], 'all', False, 'default'))

                save_file = save_folder + '/' + p_filename
                with open(save_file + '.csv', mode='w', encoding='utf-8') as fd:
                    teftxt = csv.writer(fd)
                    teftxt.writerow(['geoid20','districtname'])
                    teftxt.writerows(tef_csv_data)

                sqlstmt = "DELETE FROM users_district_tef \
                        WHERE users_customuser_id = %s \
                        AND layer_name = %s AND plan_name=%s AND sessid=%s "
                xret = clspgrawsql.getExecute(sqlstmt, [user_id,
                        p_layer, p_plan_name, str(p_sessid)], dbname='default', returnid=None)

        except Exception as e:
            print('--> swdbredist.redisapi.make_tefcsv : ', str(e.args[0]))
            print('--> swdbredist.redisapi.make_tefcsv : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdblibs.layerapi.make_tefcsv', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()

    def getInfoData(self, clspgrawsql, reqs, save_folder, p_filename, userobj=None, plocked=None):

        """
        Use for make_pdf_from_submit and make_tefcsv

        :param clspgrawsql: Postgres raw SQL class
        :param clspgrawsql: obj

        :param p_ulgid: users_own_layers.gid
        :param p_ulgid: int

        :param save_folder: destination folder
        :param save_folder: str

        :param userobj: Django user object
        :param userobj: object

        :param reqflag: {'ldata': value, 'ffpath': value, 'xsubcode': value}
        """
        # outret = {'ldata': None, 'ffpath': None, 'xsubcode': ''}
        ldata = None
        recs = None
        try:
            if('layer' in reqs.data): p_layer = bleach.clean(reqs.data.get('layer')).lower()
            else: p_layer = None
            if('plan_name' in reqs.data): p_plan_name = reqs.data.get('plan_name')
            else: p_plan_name = None
            if('sessid' in reqs.data): p_sessid = bleach.clean(reqs.data.get('sessid'))
            else: p_sessid = None
            if('ulgid' in reqs.data): p_ulgid = bleach.clean(reqs.data.get('ulgid'))
            else: p_ulgid = None
            if plocked is None: p_locked = True
            else: p_locked = False

            p_gid = None
            user_id = None
            if userobj is None and p_ulgid is not None:
                sqlstmt = 'SELECT * FROM users_own_district WHERE locked = %s and gid = %s'
                ldata = clspgrawsql.getRows(sqlstmt, [p_locked,p_ulgid], None, True, 'default')
                p_gid = str(ldata[0]['gid'])
                user_id = ldata[0]['users_customuser_id']
                p_sessid = str(ldata[0]['sessid'])
                p_plan_name = str(ldata[0]['plan_name'])
                p_layer = str(ldata[0]['layer_name'])

            elif userobj is not None:
                if userobj.username != 'coi_anonymous': p_sessid = userobj.id
                
                user_id = userobj.id
                sqlstmt = 'SELECT * FROM users_own_district  \
                        WHERE users_customuser_id = %s AND sessid = %s \
                        AND plan_name=%s AND layer_name=%s AND locked = %s'
                ldata = clspgrawsql.getRows(sqlstmt,
                        [user_id,str(p_sessid),p_plan_name,p_layer,p_locked], None, True, 'default')
                p_gid = str(ldata[0]['gid'])

            if p_gid is not None:

                # Get districts in a plan
                sqlstmt = 'SELECT * FROM ' + p_layer  + \
                   ' WHERE users_customuser_id = %s AND sessid = %s \
                    AND plan_name=%s AND locked = %s'
                recs = clspgrawsql.getRows(sqlstmt,
                    [user_id,str(p_sessid),p_plan_name,p_locked], None, True, 'default')
                
                # ldata[0] contains data for pdf front page from users_own_district
                rlab.district_front_page(save_folder, p_filename, ldata[0], recs)
        except Exception as e:
            print('--> swdbredist.redisapi.getInfoData : ', str(e.args[0]))
            print('--> swdbredist.redisapi.getInfoData : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdblibs.redisapi.getInfoData', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()
        # finally:
        #    return ldata, recs

    def create_png_from_shp(self, save_folder, pdf_file,ldata):
        """
        create .png from shapefile inside getDistrictGeoserver
        :param value: shapefile
        :param value: pngfile
        :return object:
        """
        #get the shapefile from geoserver
        # Convert shapefile to png via geopandas
        #get shapefile and read it
        try:
            os.chdir(save_folder)
            shpfile = glob.glob('*.shp')
            gdf=gpd.read_file(shpfile[0])
            # loop throut the features 
            for row in range(len(gdf.index)):
                df= gdf.loc[[row],]
                plt.figure(dpi=1000)
                df = df.to_crs(epsg=3857)
                ax=df.plot(alpha=0.5, color='pink',edgecolor='red')
                # ctx.add_basemap(ax,source=ctx.providers.Stamen.TonerLite)
                ax.set_axis_off()
                fid= df.iloc[0]['fid']
                #gid = ldata[0]['gid']
                #add gid_fid to the png filename
                afile = pdf_file +'_' + str(fid) + '.png'
                plt.savefig(afile, bbox_inches='tight')
                plt.clf()
                plt.close('all')
                # Create pdf from png
                rlab.district_detail_page(save_folder, afile, ldata, df.iloc[0])
                os.remove(afile)

        except Exception as e:
            print('--> swdbredist.redisapi.create_png_from_shp : ', str(e.args[0]))
            print('--> swdbredist.redisapi.create_png_from_shp : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdblibs.redisapi.create_png_from_shp', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()

    def get_layer_type_name(self,p_layer):
        """
        Get plan layer type from ldata 'p_layer'
        :param value: self
        :param value: p_layer
        :return string abbreviation of the layer:
        """
        if p_layer=='user_board_of_equalization':
            return 'BOE_'
        elif p_layer =='user_ca_state_assembly':
            return 'AD_'
        elif p_layer =='user_ca_state_senate':
            return 'SD_'
        elif p_layer =='user_us_house':
            return 'CD_'

    def send_email(self, save_folder, p_subemail):
        """
        Send redistricting plan submission confirmation email with attachments.
        
        This method handles email delivery for redistricting plan submissions,
        including automatic file size management to comply with email provider
        limits. It splits large attachments across multiple emails if necessary.
        
        Email Features:
        - Automatic file size checking (7MB limit per email)
        - Multiple email sending for large attachments
        - Secure SMTP authentication
        - Professional email formatting
        - Comprehensive error logging

        Args:
            save_folder (str): Full path to directory containing files to attach.
                Expected files: PDF reports, shapefiles, CSV data, documentation
            p_subemail (str): Recipient email address for submission confirmation

        Returns:
            None: Email is sent asynchronously, no return value

        Raises:
            Exception: Logged to Swdblog model for debugging purposes

        Note:
            - Uses AWS SES for email delivery (configured in EMAIL settings)
            - Files are automatically attached from the save_folder
            - Email content is configured via EMAIL environment variables
            - Maximum file size per email: 7MB (configurable)

        Example:
            >>> api = RedistApi()
            >>> api.send_email(
            ...     save_folder='/tmp/plan_export_123',
            ...     p_subemail='user@example.com'
            ... )
        """
        try:
            # Extract email configuration from environment variables
            email_sender = EMAIL['EMAIL_SENDER']
            email_sendname = EMAIL['EMAIL_SENDNAME']
            email_username_smtp = EMAIL['EMAIL_USERNAME_SMTP']
            email_password_smtp = EMAIL['EMAIL_PASSWORD_SMTP']
            email_host = EMAIL['EMAIL_HOST']
            email_port = EMAIL['EMAIL_PORT']
            coi_subject = EMAIL['COI_SUBJECT']
            coi_message = EMAIL['COI_MESSAGE']

            # Create multipart email message for attachments
            message = MIMEMultipart('alternative')
            message["From"] = email.utils.formataddr((email_sendname, email_sender))
            message["To"] = p_subemail
            message["Subject"] = coi_subject

            # Add email body content
            message.attach(MIMEText(coi_message, "plain"))
            
            # Get list of all files in the save folder
            flist = glob.glob(save_folder + '/*')
            total_size = 0
            
            # Process files with size limit checking
            # If total size exceeds 7MB, send current email and start new one
            for each_file in flist:
                # Check if adding this file would exceed size limit
                # Note: Current implementation checks total_size > 7MB before adding file
                # Alternative: if total_size + os.path.getsize(each_file) > 7000000:
                if total_size > 7000000:
                    # Log in to server using secure context and send email
                    with smtplib.SMTP(email_host, email_port) as server:
                        server.ehlo()
                        server.starttls()
                        # stmplib docs recommend calling ehlo() before & after starttls()
                        server.ehlo()
                        server.login(email_username_smtp, email_password_smtp)
                        server.sendmail(email_sender, p_subemail, message.as_string())
                        server.quit()
                        message=''
                        part =''
                        total_size = 0  

                total_size  =+ os.path.getsize(each_file)
                with open(each_file, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                    # Encode file in ASCII characters to send by email
                    encoders.encode_base64(part)
                    # Add header as key/value pair to attachment part
                    part.add_header('Content-Disposition',
                        'attachment; filename="%s"' % os.path.basename(each_file))
                    message.attach(part)

            # Whatever file left over from loop then send it all
            with smtplib.SMTP(email_host, email_port) as server:
                server.ehlo()
                server.starttls()
                # stmplib docs recommend calling ehlo() before & after starttls()
                server.ehlo()
                server.login(email_username_smtp, email_password_smtp)
                server.sendmail(email_sender, p_subemail, message.as_string())
                server.quit()

        except Exception as e:
            print('--> swdbredist.redisapi.send_email : ', str(e.args[0]))
            print('--> swdbredist.redisapi.send_email : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdblibs.redistapi.send_email', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()

    def check_exists_plan(self, userobj, clspgrawsql, reqs):
        """
        Check existing plan
        """
        outret = 0
        try:
            if('plan_name' in reqs.data): p_plan_name = bleach.clean(reqs.data.get('plan_name'))
            else: p_plan_name = None
            if('sessid' in reqs.data): p_sessid = bleach.clean(reqs.data.get('sessid'))
            else: p_sessid = None
            
            if userobj.username != 'coi_anonymous': p_sessid = userobj.id

            sqlstmt = 'SELECT * FROM users_own_district WHERE users_customuser_id=%s \
                AND sessid=%s AND plan_name=%s'
            ldata = clspgrawsql.getRows(sqlstmt,
                [userobj.id,str(p_sessid),p_plan_name], None, True, 'default')
            outret = len(ldata)

        except Exception as e:
            print('--> swdbredist.redisapi.check_exists_plan : ', str(e.args[0]))
            print('--> swdbredist.redisapi.check_exists_plan : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdblibs.redistapi.check_exists_plan', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()
        finally:
            return outret

    def add_from_template(self, userobj, clspgrawsql, reqs):
        """
        Create a new redistricting plan from a predefined template.
        
        This method allows users to start with a baseline redistricting plan
        (typically the current official districts) and modify it to create
        their own redistricting proposal. It copies all district geometries
        and demographic data from the template to the user's workspace.
        
        Template Sources:
        - crc_boe: Current Board of Equalization districts
        - crc_assembly: Current California State Assembly districts
        - crc_senate: Current California State Senate districts
        - crc_house: Current US House of Representatives districts

        Args:
            userobj (User): Django user object
            clspgrawsql (object): PostgreSQL raw SQL handler instance
            reqs (Request): Django request object containing:
                - plan_name (str): Name for the new plan
                - map_type (str): Type of map visualization
                - district_type (str): Type of district layer to create
                - sessid (str): Session identifier

        Returns:
            dict: Plan creation result containing:
                - gid (int): Unique identifier for the new plan
                - layer_name (str): Type of district layer created
                - locked (bool): False (new plans start unlocked)
                - plan_name (str): Name of the created plan
                - subcode (None): Not assigned until submission

        Raises:
            Exception: Logged to Swdblog model for debugging purposes

        Example:
            >>> api = RedistApi()
            >>> result = api.add_from_template(
            ...     userobj=request.user,
            ...     clspgrawsql=sql_handler,
            ...     reqs=request
            ... )
            >>> print(f"Created plan {result['plan_name']} with ID {result['gid']}")
        """
        outret = {}
        try:
            if('plan_name' in reqs.data): plan_name = bleach.clean(reqs.data.get('plan_name'))
            else: plan_name = None
            if('map_type' in reqs.data): p_map_type = bleach.clean(reqs.data.get('map_type')).lower()
            else: p_map_type = None
            if('district_type' in reqs.data): p_district_type = bleach.clean(reqs.data.get('district_type')).lower()
            else: p_district_type = None
            if('sessid' in reqs.data): p_sessid = bleach.clean(reqs.data.get('sessid'))
            else: p_sessid = None
            
            if userobj.username != 'coi_anonymous': p_sessid = userobj.id
            
            sqlstmt = 'INSERT INTO users_own_district (users_customuser_id, \
                sessid,layer_name,plan_name,map_type) \
                VALUES (%s,%s,%s,%s,%s) RETURNING gid'
            retval = clspgrawsql.getExecute(sqlstmt, [userobj.id,p_sessid,
                p_district_type,plan_name,p_map_type], dbname='default', returnid=True)

            # Mapping from user district tables to template (current redistricting) tables
            trans = {
                'user_board_of_equalization': 'crc_boe',
                'user_ca_state_assembly': 'crc_assembly',
                'user_ca_state_senate': 'crc_senate', 
                'user_us_house': 'crc_house'
            }
            
            # Complex INSERT...SELECT statement to copy all district data from template
            # This copies demographic data, geometries, and metadata from current districts
            # to create a starting point for user modifications
            sqlstmt = """INSERT INTO """ + p_district_type + """(users_customuser_id,sessid,cit_19, 
                nh_wht_cit_19,nh_othmr_cit_19,hsp_cit_19,cvap_19,nh_wht_cvap_19, 
                nh_othmr_cvap_19,hsp_cvap_19,doj_nh_ind_cit_19,doj_nh_ind_cvap_19, 
                doj_nh_blk_cit_19,doj_nh_blk_cvap_19,doj_nh_asn_cit_19,doj_nh_asn_cvap_19, 
                population,hispanic_origin,nh_wht,population_18,h18_pop,nh18_wht,doj_nh_blk, 
                doj_nh_ind,doj_nh_asn,doj_nh_hwn,doj_nh_oth,doj_nh_othmr,doj_nh18_blk, 
                doj_nh18_ind,doj_nh18_asn,doj_nh18_hwn,doj_nh18_oth,doj_nh18_othmr, 
                percideal,percentlatinopop,percentwhitepop,percentblack,percentasian, 
                percentmmr,vapercentlatino,vapercentwhite,vapercentblack,vapercentasian, 
                vapercentmmr,cvappercentlatino,cvappercentwhite,cvappercentblack, 
                cvappercentasian,cvappercentmmrnh,district_type,districtname, 
                locked, comments,plan_name,map_type, geom, 
                users_own_district_gid) SELECT %s,%s,cit_19, 
                nh_wht_cit_19,nh_othmr_cit_19,hsp_cit_19,cvap_19,nh_wht_cvap_19, 
                nh_othmr_cvap_19,hsp_cvap_19,doj_nh_ind_cit_19,doj_nh_ind_cvap_19, 
                doj_nh_blk_cit_19,doj_nh_blk_cvap_19,doj_nh_asn_cit_19,doj_nh_asn_cvap_19, 
                population,hispanic_origin,nh_wht,population_18,h18_pop,nh18_wht,doj_nh_blk, 
                doj_nh_ind,doj_nh_asn,doj_nh_hwn,doj_nh_oth,doj_nh_othmr,doj_nh18_blk, 
                doj_nh18_ind,doj_nh18_asn,doj_nh18_hwn,doj_nh18_oth,doj_nh18_othmr, 
                percideal,percentlatinopop,percentwhitepop,percentblack,percentasian, 
                percentmmr,vapercentlatino,vapercentwhite,vapercentblack,vapercentasian, 
                vapercentmmr,cvappercentlatino,cvappercentwhite,cvappercentblack, 
                cvappercentasian,cvappercentmmrnh,%s,districtname,locked, comments, 
                %s,%s,geom,%s FROM """ + trans[p_district_type]
            xret = clspgrawsql.getExecute(sqlstmt, [userobj.id,p_sessid,p_district_type,
                plan_name,p_map_type,retval['ReturnId']], dbname='default', returnid=None)

            # Get all district FIDs for the newly created plan
            sqlstmt = 'SELECT fid FROM ' + p_district_type + ' WHERE users_own_district_gid=%s'
            recs = clspgrawsql.getRows(sqlstmt, [retval['ReturnId']], None, True, 'default')

            # Assign sequential color codes for map visualization
            # Colors cycle from 001 to 024, then restart at 001
            # This ensures each district has a unique visual identifier
            color = 0
            sqlstmt = 'UPDATE ' + p_district_type + ' SET stylecolor=%s WHERE fid = %s'
            for c in recs:
                if color < 24: 
                    color = color + 1
                else: 
                    color = 1  # Reset to 1 after reaching 24

                # Format color as 3-digit zero-padded string (e.g., "001", "024")
                pcolor = str(color).zfill(3)
                xret = clspgrawsql.getExecute(sqlstmt, [pcolor, c['fid']], dbname='default', returnid=None)

            outret = {'gid': retval['ReturnId'], 'layer_name': p_district_type,
                      'locked': False, 'plan_name': plan_name, 'subcode': None}
        except Exception as e:
            print('--> swdbredist.redisapi.add_from_template : ', str(e.args[0]))
            print('--> swdbredist.redisapi.add_from_template : ', traceback.format_exc())
            swdblog = Swdblog(logtype='E', logmethod='swdblibs.redistapi.add_from_template', created_on=timezone.now(), messages=str(e.args[0]))
            swdblog.save()
        finally:
            return outret



