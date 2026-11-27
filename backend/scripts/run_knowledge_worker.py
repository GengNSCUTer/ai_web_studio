import logging

from app.services.knowledge_job_runtime import create_knowledge_worker


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("app.services.knowledge_job_runtime").setLevel(logging.INFO)
    logging.getLogger("app.services.agent_graph.knowledge_index_graph").setLevel(logging.INFO)
    create_knowledge_worker().run_forever()


if __name__ == "__main__":
    main()
