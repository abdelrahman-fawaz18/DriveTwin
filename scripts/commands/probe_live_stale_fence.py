"""Qualification-only replacement of the token sent to the real fence endpoint."""

from urllib.error import HTTPError

from evdt.live_speed_command import LiveSpeedCommand
from evdt.monitoring import adapter


def main():
    real_post = LiveSpeedCommand.post

    def post(self, endpoint, value):
        if endpoint != "verify-fence":
            return real_post(self, endpoint, value)
        generation = value["generation"] - 1
        try:
            return real_post(self, endpoint, {**value, "generation": generation})
        except HTTPError as error:
            if error.code == 409:
                print(f"EV-DT stale live fence {generation} refused", flush=True)
            raise

    LiveSpeedCommand.post = post
    adapter.main()


if __name__ == "__main__":
    main()
