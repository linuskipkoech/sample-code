# Redistricting API - Geospatial Data Processing Sample

## Overview
This is a sample code from projects I've worked on, demonstrating advanced geospatial data processing capabilities in Django. The `redistapi.py` module showcases integration of multiple geographic data processing technologies, from PostGIS database operations to GeoServer interactions.

## Key Technical Features

### 🗺️ **Geospatial Data Processing**
- **PostGIS Integration**: Complex spatial queries using ST_Contains, bounding box intersections
- **GeoServer WFS/WMS**: Dynamic map service integration for data visualization
- **Multi-format Export**: Shapefiles, CSV, PDF generation with spatial data
- **TEF Generation**: Tabular Equivalent File creation for redistricting compliance

### 🏗️ **Architecture & Performance**
- **Multi-threaded Processing**: Concurrent TEF generation using ThreadPoolExecutor
- **Database Optimization**: Efficient spatial indexing and query optimization
- **File Management**: Secure file handling with size limits and cleanup
- **Caching Strategy**: Redis-ready implementation for performance scaling

### 🔒 **Security & Data Integrity**
- **Input Sanitization**: XSS prevention using bleach.clean()
- **User Isolation**: Session-based access control for anonymous users
- **File Security**: Secure file paths and access controls
- **Error Handling**: Comprehensive logging and exception management

### 📊 **Business Logic**
- **Plan Management**: Create, modify, and export redistricting plans
- **Template System**: Start from current districts and modify
- **Email Notifications**: Automated submission confirmations with attachments
- **Multi-user Support**: Both authenticated and anonymous user workflows

## Technology Stack
- **Backend**: Django 2.1+ with PostgreSQL/PostGIS
- **Geospatial**: GeoServer, PostGIS spatial functions
- **Processing**: Python, GeoPandas, Matplotlib
- **Infrastructure**: AWS SES, Redis (recommended)
- **Security**: Keycloak integration, CSRF protection

## Code Highlights

### Spatial Query Example
```python
# Efficient spatial intersection with PostGIS
sqlstmt = """SELECT c.geoid20, u.districtname 
    FROM tmp_user_ca_state_assembly u, coi_centroids c 
    WHERE (u.geom && c.geom) AND ST_Contains(u.geom, c.geom) 
    AND u.users_customuser_id = %s AND u.plan_name = %s"""
```

### Multi-threaded Processing
```python
# Parallel TEF generation for performance
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(self.thread_make_tef, args) for args in districts]
```

### GeoServer Integration
```python
# Dynamic WFS URL construction for data export
server_url = f"{base_url}?service=WFS&request=GetFeature&typeName={workspace}:{layer}"
```

## Use Cases
- **Redistricting Applications**: Community of Interest mapping and plan creation
- **Geospatial Web Services**: Dynamic map generation and data export
- **Civic Technology**: Public engagement tools for redistricting processes
- **Data Analysis**: Demographic analysis and compliance checking

## Performance Considerations
- Implements connection pooling for database operations
- Uses spatial indexing for efficient geometric queries
- Multi-threaded file processing for large datasets
- Configurable caching layers for frequently accessed data

## Security Features
- Input validation and sanitization
- User session management
- Secure file handling and cleanup
- API rate limiting capabilities
- CSRF and XSS protection

---

*This code demonstrates proficiency in geospatial data processing, Django development, and production-ready application architecture. It showcases integration of multiple technologies to solve complex real-world problems in civic technology and redistricting applications.*
