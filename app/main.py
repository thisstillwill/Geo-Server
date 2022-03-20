from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
import aioredis
import ulid
from datetime import datetime, timedelta

REDIS_URL: str = "redis://redis:6379"
EXPIRE_TIME_HOURS: int = 24
SEARCH_RADIUS_METERS: int = 1600

app = FastAPI()
redis = aioredis.from_url(REDIS_URL, decode_responses=True)

# Add a new point from a client
@app.post("/points")
async def add_point(request: Request, status_code=201):
    # Process JSON and create identifier
    point = await request.json()
    point["id"] = ulid.new().str

    # Store point as hash using id as the key
    await redis.hset(point["id"], mapping=point)

    # Set expire time
    expire_time = datetime.now() + timedelta(hours=EXPIRE_TIME_HOURS)
    await redis.expireat(point["id"], expire_time)

    # Register point as geospatial item to Redis using id as name
    await redis.geoadd("points", point["longitude"], point["latitude"], point["id"])

# Return a list of points within a radius of the given location
@app.get("/points")
async def get_points(latitude: float, longitude: float, radius: float, status_code=200):
    # Find which points are within the search radius
    point_keys = await redis.georadius("points", longitude, latitude, radius, unit="m")

    # Create an array of points to return
    points = []

    # Add  each point's corresponding information to the dictionary
    for point_key in point_keys:

        # Remove the key from the sorted set if it has already expired
        if not await redis.exists(point_key):
            print("This key has expired! Deleting from sorted set...")
            await redis.zrem("points", point_key)
        else:
            point = await redis.hgetall(point_key)
            point["latitude"] = float(point["latitude"])
            point["longitude"] = float(point["longitude"])
            points.append(point)
    return points
