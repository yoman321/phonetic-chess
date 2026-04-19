import os

import chess
from dotenv import load_dotenv
from neo4j import GraphDatabase
from seed.seed_db import ensure_seeded

load_dotenv()

NEO4J_URI = f"bolt://{os.environ['NEO4J_HOST']}:{os.environ['NEO4J_PORT']}"
NEO4J_USER = os.environ.get("NEO4J_USER")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")


def lookup_move(driver, fen, phrase):
    """Query Neo4j for the chess move associated with a phrase from a given position."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (p:Position {fen: $fen})-[m:MOVE]->(c:Position)
            WHERE toLower(m.phrase) = toLower($phrase)
            RETURN m.uci AS uci, m.san AS san, m.opening AS opening
            """,
            fen=fen,
            phrase=phrase,
        ).single()
        if result:
            return result["uci"], result["san"], result["opening"]
        return None, None, None


def get_available_phrases(driver, fen):
    """Get all mapped phrases from the current position."""
    with driver.session() as session:
        results = session.run(
            """
            MATCH (p:Position {fen: $fen})-[m:MOVE]->(c:Position)
            RETURN m.uci AS uci, m.san AS san, m.phrase AS phrase, m.opening AS opening
            """,
            fen=fen,
        )
        return [dict(r) for r in results]


def main():
    auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
    driver = GraphDatabase.driver(NEO4J_URI, auth=auth)
    driver.verify_connectivity()
    print("Connected to Neo4j.\n")

    ensure_seeded(driver)

    board = chess.Board()
    turn_names = {chess.WHITE: "White", chess.BLACK: "Black"}

    print("\n=== Phonetic Chess ===")
    print("Type conversational phrases to make chess moves.")
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
            available = get_available_phrases(driver, board.fen())
            if available:
                print("\nAvailable phrases from this position:")
                for m in available:
                    print(f"  \"{m['phrase']}\" → {m['san']}  [{m['opening']}]")
                print()
            else:
                print("  No mapped phrases from this position (off-book).\n")
            continue

        uci, san, opening = lookup_move(driver, board.fen(), raw)
        if uci is None:
            print("  Hmm, I don't know that one. Try 'phrases' to see options.\n")
            continue

        move = chess.Move.from_uci(uci)
        board.push(move)
        print(f"  → {san}  [{opening}]")
        print()

    driver.close()
    print("Game over!" if board.is_game_over() else "")


if __name__ == "__main__":
    main()
