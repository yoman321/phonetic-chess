"""Play a real game through say_move against live Groq, so the call log fills."""
import os, sys, time, uuid

sys.path.insert(0, os.path.abspath("backend"))
from dotenv import load_dotenv
load_dotenv("backend/.env")
os.environ["DATABASE_URL"] = "postgresql://phonetic:phonetic@127.0.0.1:55432/phonetic_chess"

import psycopg
from psycopg.rows import dict_row
import db as dbmod
from controller_operations import sessions_ops
from controller_operations.errors import ApiError

class FakeSocketIO:
    def emit(self, *a, **k):
        pass

MESSAGES = [
    "let's go, I'm coming right at you",
    "hmm, I need to be careful here",
    "ha! you didn't see that coming did you",
    "I'm feeling nervous about this position",
    "time to attack, no more waiting",
    "just quietly developing, nothing to see",
    "I'm going to crush you now",
    "ugh this is getting complicated",
    "playful little sidestep for you",
    "confident and centred, holding the middle",
    "getting desperate, throwing everything at it",
    "calm, patient, I can wait all day",
    "sharp threat incoming, watch out",
    "defending carefully, no risks",
    "bold sacrifice, let's see what happens",
    "steady as she goes",
    "aggressive push down the flank",
    "cautious retreat to regroup",
    "one more strike and it's over",
    "resigned but still fighting",
]

sid = f"costrun-{uuid.uuid4().hex[:8]}"
conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row)
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
with conn.cursor() as cur:
    cur.execute(
        "INSERT INTO sessions (id, fen, white_token, black_token) VALUES (%s,%s,%s,%s)",
        (sid, START, f"w-{sid}", f"b-{sid}"),
    )
print("session", sid, flush=True)

sio = FakeSocketIO()
for i, msg in enumerate(MESSAGES):
    token = f"w-{sid}" if i % 2 == 0 else f"b-{sid}"
    t0 = time.time()
    try:
        res = sessions_ops.say_move(dbmod.connect, sio, sid, msg, token)
        print(f"ply {i+1:2d} {'W' if i%2==0 else 'B'} {res['uci']:6s} "
              f"{res.get('san',''):8s} {int((time.time()-t0)*1000):5d}ms  {msg[:32]}",
              flush=True)
        if res.get("status") != "active":
            print("game ended:", res.get("status"), flush=True)
            break
    except ApiError as e:
        print(f"ply {i+1:2d} ApiError {e.code}", flush=True)
        if e.code != "llm_bad_response":
            break
print("SESSION_ID=" + sid)
