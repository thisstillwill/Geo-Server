from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
from redis_om import JsonModel
from redis_om import get_redis_connection

REDIS_DATA_URL = "redis://localhost:6379"

class Point(JsonModel):
    title: str

app = FastAPI()

@app.post("/points")
async def read_root(request: Request, response: Response):
    print(await request.json())

@app.on_event("startup")
async def startup():
    Point.Meta.database = get_redis_connection(url=REDIS_DATA_URL, decode_responses=True)
