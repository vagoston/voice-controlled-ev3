"""CRY-6: confirms LiveKit Cloud credentials in .env are valid.

Mints an access token and calls the server API to list rooms (empty is fine —
we're just checking the key/secret/URL are accepted).
"""

import asyncio

from dotenv import load_dotenv
from livekit import api

load_dotenv()


async def main() -> None:
    token = (
        api.AccessToken()
        .with_identity("connectivity-test")
        .with_grants(api.VideoGrants(room_join=True, room="connectivity-test-room"))
        .to_jwt()
    )
    print(f"Minted token OK ({len(token)} chars)")

    lkapi = api.LiveKitAPI()
    try:
        rooms = await lkapi.room.list_rooms(api.ListRoomsRequest())
        print(f"Connected to LiveKit Cloud. Current rooms: {[r.name for r in rooms.rooms]}")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
