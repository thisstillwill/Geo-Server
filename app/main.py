from fastapi import Depends, HTTPException, status, FastAPI
from starlette.requests import Request
from starlette.responses import Response
import aioredis
import ulid
from datetime import datetime, timedelta
import jwt
from jwt.algorithms import RSAAlgorithm
import json
import httpx

REDIS_URL: str = "redis://redis:6379"
EXPIRE_TIME_HOURS: int = 24

APPLE_PUBLIC_KEYS_URL = "https://appleid.apple.com/auth/keys"
APPLE_PUBLIC_KEYS = None
APPLE_KEY_CACHE_EXP = 60 * 60 * 24
APPLE_LAST_KEY_FETCH = 0

APPLE_APP_ID = "com.williamsvoboda.Geo"
APPLE_ISSUER = "https://appleid.apple.com"

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
    )

app = FastAPI()
redis = aioredis.from_url(REDIS_URL, decode_responses=True)

# Find the matching Apple public key given its kid
async def _fetch_apple_public_key(kid: str):
    # Check to see if the public key is unset or is stale before returning
    global APPLE_LAST_KEY_FETCH
    global APPLE_PUBLIC_KEYS

    if (APPLE_LAST_KEY_FETCH + APPLE_KEY_CACHE_EXP) < int(datetime.timestamp(datetime.now())) or APPLE_PUBLIC_KEYS is None:
        client = httpx.AsyncClient()
        response = await client.get(APPLE_PUBLIC_KEYS_URL)
        APPLE_PUBLIC_KEYS = response.json()["keys"]
    
    key_body = [key for key in APPLE_PUBLIC_KEYS if key["kid"] == kid][0]
    public_key = RSAAlgorithm.from_jwk(json.dumps(key_body))
    APPLE_LAST_KEY_FETCH = int(datetime.timestamp(datetime.now()))

    return public_key

# Verify a user's token from Sign in with Apple
async def verify_identity_token(request: Request) -> bool:
    identity_token = request.headers["Authorization"]
    token_header = jwt.get_unverified_header(request.headers["Authorization"])
    kid = token_header["kid"]
    public_key = await _fetch_apple_public_key(kid)
    try:
        jwt.decode(identity_token, public_key, issuer=APPLE_ISSUER, audience=APPLE_APP_ID, algorithms=["RS256"])
        print("Verified identity token!")
        return True
    except jwt.exceptions.ExpiredSignatureError as e:
        print("Identity token has expired!")
        return False
    except jwt.exceptions.InvalidAudienceError as e:
        print("Identity token's audience did not match!")
        return False
    except Exception as e:
        print(e)
        return False

# Register a new user
@app.post("/users")
async def sign_up(request: Request, verified_identity_token = Depends(verify_identity_token)):
    if not verified_identity_token:
         raise credentials_exception
    user = await request.json()
    print(user)

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
