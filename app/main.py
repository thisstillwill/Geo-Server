import re
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
import aioredis
import ulid

REDIS_URL: str = "redis://redis:6379"
SEARCH_RADIUS: int = 400

app = FastAPI()
redis = aioredis.from_url(REDIS_URL, decode_responses=True)

# Add a new point from a client
@app.post("/points")
async def add_point(request: Request, status_code=201):
    # Process JSON and create identifier
    point_json = await request.json()
    point_body = point_json["point"]
    point_body["id"] = ulid.new().str

    # Store point as hash using id as the key
    await redis.hset(point_body["id"], mapping=point_body)

    # Register point as geospatial item to Redis using id as name
    await redis.geoadd("points", point_body["longitude"], point_body["latitude"], point_body["id"])

# Return a list of points within a radius of the given location
@app.get("/points")
async def get_points(latitude: float, longitude: float, status_code=200):
    # Find which points are within the search radius
    point_keys = await redis.georadius("points", longitude, latitude, SEARCH_RADIUS, unit="m")

    # Create a dictionary of points to return
    points = {}

    # Add  each point's corresponding information to the dictionary
    for point_key in point_keys:
        point_body = await redis.hgetall(point_key)
        point = {"point": point_body}
        points.update(point)
    return points
