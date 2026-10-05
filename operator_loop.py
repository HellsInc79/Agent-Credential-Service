"""Keep an operator process ready to submit jobs; JWT renewal is automatic."""

from getpass import getpass

from app.agent_client import AgentPlatformClient


def main():
    base_url = input("Platform URL [http://127.0.0.1:8000]: ").strip() or "http://127.0.0.1:8000"
    api_key = getpass("Operator agent API key: ").strip()
    if not api_key:
        print("No API key provided.")
        return

    print("Connected. Enter a team task, or type /quit to stop.")
    with AgentPlatformClient(base_url, api_key) as client:
        while True:
            objective = input("team> ").strip()
            if objective.lower() in {"/quit", "/exit"}:
                break
            if not objective:
                continue
            try:
                result = client.orchestrate(objective)
                print(f"\nController: {result['controller']['name']} — {result['status']}")
                print(result["final_answer"])
            except RuntimeError as exc:
                print(f"Request could not complete: {exc}")
            print()


if __name__ == "__main__":
    main()
