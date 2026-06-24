"""call-gateway entrypoint.

Dev mode (default): play a local WAV -> S16LE 16k mono chunks -> Soniox WS -> tokens
                    -> contracts.TranscriptPayload -> Envelope -> Kafka(TRANSCRIPT).
Live mode: Recall.ai bot joins Meet -> audio_separate_raw (base64 S16LE 16k mono per
           participant) -> Soniox -> transcript (speaker from Recall participant).

TODO:
  - [ ] dev: WAV reader -> 3840-byte (~120ms) chunks
  - [ ] Soniox WS client (model = SONIOX_MODEL); emit partial + final tokens
  - [ ] map tokens -> TranscriptPayload -> Envelope(type=transcript.final|partial) -> producer.send(TRANSCRIPT)
  - [ ] live: Recall create_bot + websocket_audio_destination_url receiver
  - [ ] meeting.events (join/leave/mute) -> Kafka(MEETING_EVENTS)
  - [ ] TTS out: consume a 'speak' instruction -> TTS -> Recall output-audio
"""

import asyncio


async def main() -> None:
    # from shared import config
    # from shared.kafka import make_producer
    print("call-gateway: TODO — dev-mode WAV -> Soniox -> Kafka(meeting.transcript)")


if __name__ == "__main__":
    asyncio.run(main())
