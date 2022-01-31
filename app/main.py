from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
import aioredis
import ulid

REDIS_URL: str = "redis://redis:6379"

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

