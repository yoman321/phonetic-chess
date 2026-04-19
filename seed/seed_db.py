import chess
from sentence_transformers import SentenceTransformer
from seed.openings import OPENING_LINES

MODEL_NAME = "all-MiniLM-L6-v2"


def is_seeded(driver):
    """Check if the database already has opening data with embeddings."""
    with driver.session() as session:
        result = session.run(
            "MATCH (p:Position {is_root: true}) RETURN count(p) AS c"
        ).single()
        if result["c"] == 0:
            return False
        # Also check that embeddings exist
        result = session.run(
            "MATCH ()-[m:MOVE]->() WHERE m.embedding IS NOT NULL RETURN count(m) AS c"
        ).single()
        return result["c"] > 0


def seed_database(driver):
    """Create the opening tree graph in Neo4j with phrase embeddings."""
    print(f"Loading embedding model ({MODEL_NAME})...")
    model = SentenceTransformer(MODEL_NAME)

    # Collect all unique phrases to embed in one batch
    all_phrases = set()
    for line in OPENING_LINES:
        for phrase in line["phrases"]:
            all_phrases.add(phrase)
    all_phrases = list(all_phrases)

    print(f"Embedding {len(all_phrases)} unique phrases...")
    embeddings = model.encode(all_phrases)
    phrase_to_embedding = {p: embeddings[i].tolist() for i, p in enumerate(all_phrases)}

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
                embedding = phrase_to_embedding[phrase]

                session.run(
                    """
                    MERGE (parent:Position {fen: $parent_fen})
                    MERGE (child:Position {fen: $child_fen})
                    MERGE (parent)-[m:MOVE {uci: $uci}]->(child)
                    SET m.phrase = $phrase,
                        m.san = $san,
                        m.opening = $opening,
                        m.embedding = $embedding
                    """,
                    parent_fen=parent_fen,
                    child_fen=child_fen,
                    uci=uci,
                    san=san,
                    phrase=phrase,
                    opening=line["name"],
                    embedding=embedding,
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
