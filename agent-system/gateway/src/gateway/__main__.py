"""gateway entrypoint — thin FE-facing bridge.

REST for FE commands (start bot, ask, approve task) -> produce to Kafka.
WS /stream?meeting_id= -> bridge meeting.transcript + agent.results + activity -> browser.
The FE team builds the Meet-overlay UI on top of this; they never touch Kafka directly.

TODO:
  - [ ] FastAPI app: POST /commands, POST /ask, POST /tasks/{id}/approve
  - [ ] WS /stream: consume(TRANSCRIPT, RESULTS) filtered by meeting_id -> forward JSON
  - [ ] auth on the FE connection
  - [ ] run with uvicorn
"""


def main() -> None:
    # import uvicorn
    # uvicorn.run("gateway.app:app", host="0.0.0.0", port=8080)
    print("gateway: TODO — FastAPI REST + Kafka->WS bridge for the frontend")


if __name__ == "__main__":
    main()
