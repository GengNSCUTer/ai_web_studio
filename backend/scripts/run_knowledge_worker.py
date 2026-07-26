from app.services.knowledge_job_runtime import create_knowledge_worker


def main() -> None:
    create_knowledge_worker().run_forever()


if __name__ == "__main__":
    main()
