from ast import Str
from urllib import request
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

APPLE_APP_ID = "com.williamsvoboda.Geo"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_PUBLIC_KEYS_URL = "https://appleid.apple.com/auth/keys"
APPLE_PUBLIC_KEYS = None
APPLE_KEY_CACHE_EXP = 60 * 60 * 24
APPLE_LAST_KEY_FETCH = 0

APP_ISSUER = "geo-apiserver"
with open("app/AuthKey_MUCTYQ2J25.p8", "r") as keyfile:
    APP_KEY = keyfile.read()
APP_KEY_ID = "MUCTYQ2J25"

credentials_exception = HTTPException(
    status_code=401,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

user_missing_exception = HTTPException(
    status_code=404,
    detail="User not found in database",
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
async def verify_identity_token(request: Request):
    identity_token = request.headers["Authorization"]
    token_header = jwt.get_unverified_header(request.headers["Authorization"])
    kid = token_header["kid"]
    public_key = await _fetch_apple_public_key(kid)
    try:
        decoded_token = jwt.decode(identity_token, public_key, issuer=APPLE_ISSUER, audience=APPLE_APP_ID, algorithms=["RS256"])
        sub = decoded_token["sub"]
        user_id = (await request.json())["id"]
        if sub != user_id:
            print("User ID does not match token subject!")
            raise credentials_exception
        print("Verified identity token!")
    except jwt.exceptions.ExpiredSignatureError as e:
        print("Identity token has expired!")
        raise credentials_exception
    except jwt.exceptions.InvalidAudienceError as e:
        print("Identity token's audience did not match!")
        raise credentials_exception
    except Exception as e:
        print(e)
        raise credentials_exception

# Verify a user exists in the database
async def verify_user_exists(request: Request):
    if not await redis.exists((await request.json())["id"]):
        raise user_missing_exception

# Generate a refresh token for verified users
async def generate_refresh_token(sub: str) -> str:
    iss = APP_ISSUER
    aud = APPLE_APP_ID
    iat = round(datetime.now().timestamp())
    exp = round((datetime.now() + timedelta(weeks=4)).timestamp())
    kid = APP_KEY_ID
    refresh_token = jwt.encode({"iss": iss, "aud": aud, "exp": exp, "iat": iat, "sub": sub}, APP_KEY, algorithm="HS256", headers={"kid": kid, "alg": "HS256"})
    return refresh_token

# Verify a user's refresh token
async def verify_refresh_token(request: Request):
    refresh_token = request.headers["Authorization"]
    try:
        decoded_token = jwt.decode(refresh_token, APP_KEY, issuer=APP_ISSUER, audience=APPLE_APP_ID, algorithms=["HS256"])
        sub = decoded_token["sub"]
        user_id = (await request.json())["id"]
        if sub != user_id:
            print("User ID does not match token subject!")
            raise credentials_exception
        print("Verified refresh token!")
    except jwt.exceptions.ExpiredSignatureError as e:
        print("Refresh token has expired!")
        raise credentials_exception
    except jwt.exceptions.InvalidAudienceError as e:
        print("Refresh token's audience did not match!")
        raise credentials_exception
    except Exception as e:
        print(e)
        raise credentials_exception

# Register a new user
@app.post("/users", dependencies=[Depends(verify_identity_token)])
async def sign_up(request: Request):
    # Store user as hash using id as the key
    user = await request.json()
    await redis.hset(user["id"], mapping=user)
    print("Signed up!")
    print("Returning refresh token!")
    return {"token": await generate_refresh_token(user["id"])}
    
# Verify a returning user and start a new session
@app.post("/auth", dependencies=[Depends(verify_user_exists), Depends(verify_identity_token)])
async def sign_in(request: Request):
    print("Signed in!")
    print("Returning user info and refresh token!")
    response = {}
    user_id = (await request.json())["id"]
    user = await redis.hgetall(user_id)
    response.update({"user": user})
    response.update({"token": {"token": await generate_refresh_token(user_id)}})
    return response
    # print("Returning refresh token!")
    # return {"token": await generate_refresh_token((await request.json())["id"])}

# Verify a returning user's session
@app.post("/session", dependencies=[Depends(verify_user_exists), Depends(verify_refresh_token)])
async def sign_in(request: Request):
    print("Verified previous session!")
    print("Returning user info!")
    user_id = (await request.json())["id"]
    return await redis.hgetall(user_id)

# Add a new point from a client
@app.post("/points")
async def add_point(request: Request):
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
