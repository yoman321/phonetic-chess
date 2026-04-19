import os

import chess
import numpy as np
from dotenv import load_dotenv
from neo4j import GraphDatabase
from sentence_transformers import SentenceTransformer
from seed.seed_db import MODEL_NAME, ensure_seeded

load_dotenv()

NEO4J_URI = f"bolt://{os.environ['NEO4J_HOST']}:{os.environ['NEO4J_PORT']}"
NEO4J_USER = os.environ.get("NEO4J_USER")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")

SIMILARITY_THRESHOLD = 0.35


def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def get_candidates(driver, fen):
    """Get all moves with embeddings from the current position."""
    with driver.session() as session:
        results = session.run(
            """
            MATCH (p:Position {fen: $fen})-[m:MOVE]->(c:Position)
            RETURN m.uci AS uci, m.san AS san, m.phrase AS phrase,
                   m.opening AS opening, m.embedding AS embedding
            """,
            fen=fen,
        )
        return [dict(r) for r in results]


def find_best_match(model, user_input, candidates):
    """Embed user input and find the closest matching phrase."""
    user_embedding = model.encode(user_input)

    best = None
    best_score = -1

    for c in candidates:
        score = cosine_similarity(user_embedding, np.array(c["embedding"]))
        if score > best_score:
            best_score = score
            best = c

    if best and best_score >= SIMILARITY_THRESHOLD:
        return best, best_score
    return None, best_score


def main():
    auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
    driver = GraphDatabase.driver(NEO4J_URI, auth=auth)
    driver.verify_connectivity()
    print("Connected to Neo4j.\n")

    ensure_seeded(driver)

    print(f"Loading embedding model ({MODEL_NAME})...")
    model = SentenceTransformer(MODEL_NAME)

    board = chess.Board()
    turn_names = {chess.WHITE: "White", chess.BLACK: "Black"}

    print("\n=== Phonetic Chess ===")
    print("Say anything — we'll find the closest matching move.")
    print("Type 'phrases' to see options, 'quit' to exit.\n")

    while not board.is_game_over():
        side = turn_names[board.turn]
        raw = input(f"[{side}] Say something: ").strip()

        if not raw:
            continue

        if raw.lower() == "quit":
            print("Thanks for chatting! Game abandoned.")
            break

        if raw.lower() == "phrases":
            candidates = get_candidates(driver, board.fen())
            if candidates:
                print("\nAvailable phrases from this position:")
                for c in candidates:
                    print(f"  \"{c['phrase']}\" → {c['san']}  [{c['opening']}]")
                print()
            else:
                print("  No mapped phrases from this position (off-book).\n")
            continue

        candidates = get_candidates(driver, board.fen())
        if not candidates:
            print("  No moves mapped from this position (off-book).\n")
            continue

        match, score = find_best_match(model, raw, candidates)

        if match:
            move = chess.Move.from_uci(match["uci"])
            board.push(move)
            print(f"  Matched: \"{match['phrase']}\" (similarity: {score:.2f})")
            print(f"  → {match['san']}  [{match['opening']}]")
        else:
            print(f"  No close match found (best similarity: {score:.2f}). Try something else.")
        print()

    driver.close()
    print("Game over!" if board.is_game_over() else "")


if __name__ == "__main__":
    main()
