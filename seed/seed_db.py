import chess
from seed.openings import OPENING_LINES


def is_seeded(driver):
    """Check if the database already has opening data."""
    with driver.session() as session:
        result = session.run(
            "MATCH (p:Position {is_root: true}) RETURN count(p) AS c"
        ).single()
        return result["c"] > 0


def seed_database(driver):
    """Create the opening tree graph in Neo4j."""
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")

        session.run(
            "CREATE (:Position {fen: $fen, is_root: true})",
            fen=chess.STARTING_FEN,
        )

        for line in OPENING_LINES:
            board = chess.Board()
            for i, uci in enumerate(line["moves"]):
                parent_fen = board.fen()
                move = chess.Move.from_uci(uci)
                san = board.san(move)
                board.push(move)
                child_fen = board.fen()
                phrase = line["phrases"][i]

                session.run(
                    """
                    MERGE (parent:Position {fen: $parent_fen})
                    MERGE (child:Position {fen: $child_fen})
                    MERGE (parent)-[m:MOVE {uci: $uci}]->(child)
                    SET m.phrase = $phrase,
                        m.san = $san,
                        m.opening = $opening
                    """,
                    parent_fen=parent_fen,
                    child_fen=child_fen,
                    uci=uci,
                    san=san,
                    phrase=phrase,
                    opening=line["name"],
                )

        result = session.run(
            "MATCH (n:Position) RETURN count(n) AS positions"
        ).single()
        positions = result["positions"]
        result = session.run(
            "MATCH ()-[r:MOVE]->() RETURN count(r) AS moves"
        ).single()
        moves = result["moves"]
        print(f"Seeded graph: {positions} positions, {moves} move edges.")


def ensure_seeded(driver):
    """Seed the database only if it hasn't been seeded yet."""
    if is_seeded(driver):
        print("Database already seeded, skipping.")
    else:
        print("Seeding database...")
        seed_database(driver)
