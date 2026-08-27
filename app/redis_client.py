import os

from dotenv import load_dotenv
import redis

load_dotenv()

REDIS_URL = os.environ["REDIS_URL"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)

DELIVERY_QUEUE = "delivery_queue"
RETRY_QUEUE = "retry_queue"
DEAD_LETTER_QUEUE = "dead_letter_queue"
