from mfrc522 import MFRC522
import RPi.GPIO as GPIO
import logging
import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from time import sleep
from dotenv import load_dotenv
import tkinter as tk
from PIL import Image, ImageTk, ImageDraw
import requests
from io import BytesIO
import threading
import random
import subprocess

# ---------------- DISPLAY SETUP ----------------

root = tk.Tk()
root.attributes("-fullscreen", True)
root.configure(bg="black")

canvas = tk.Canvas(root, width=800, height=480, bg="black", highlightthickness=0)
canvas.pack(fill="both", expand=True)

RECORD_SIZE = 455
CENTER_X = 400
CENTER_Y = 250

SPIN_FRAMES = 90
SPIN_DELAY = 33

current_volume = 70

album_frames = []
record_item = None
spin_frame_index = 0
is_spinning = False
is_paused = True

spotify_client = None
spotify_device_id = None


# ---------------- HELPERS ----------------

def run_in_thread(func):
    threading.Thread(target=func, daemon=True).start()


def shutdown_pi():
    print("Shutting down PiDeck safely...")

    try:
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
    except Exception as e:
        print(f"Shutdown error: {e}")


# ---------------- VINYL IMAGE ----------------

def make_record_image(img):
    img = img.resize((RECORD_SIZE, RECORD_SIZE), Image.LANCZOS).convert("RGBA")

    mask = Image.new("L", (RECORD_SIZE, RECORD_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, RECORD_SIZE, RECORD_SIZE), fill=255)

    record = Image.new("RGBA", (RECORD_SIZE, RECORD_SIZE), (0, 0, 0, 0))
    record.paste(img, (0, 0), mask)

    draw = ImageDraw.Draw(record)
    c = RECORD_SIZE // 2
    hole = 35

    draw.ellipse(
        (c - hole, c - hole, c + hole, c + hole),
        fill=(10, 10, 10, 255)
    )

    return record


def show_image_from_url(image_url):
    global album_frames, record_item, spin_frame_index

    try:
        response = requests.get(image_url, timeout=10)
        img = Image.open(BytesIO(response.content))

        base_img = make_record_image(img)
        new_frames = []

        for i in range(SPIN_FRAMES):
            angle = -360 * i / SPIN_FRAMES
            rotated = base_img.rotate(angle, resample=Image.BICUBIC)
            new_frames.append(ImageTk.PhotoImage(rotated))

        album_frames = new_frames
        spin_frame_index = 0

        canvas.delete("record")

        record_item = canvas.create_image(
            CENTER_X,
            CENTER_Y,
            image=album_frames[0],
            tags="record"
        )

    except Exception as e:
        print(f"Image load error: {e}")


def spin_record():
    global spin_frame_index

    if is_spinning and album_frames and record_item is not None:
        spin_frame_index = (spin_frame_index + 1) % len(album_frames)
        canvas.itemconfig(record_item, image=album_frames[spin_frame_index])

    root.after(SPIN_DELAY, spin_record)


def start_spinning():
    global is_spinning
    is_spinning = True


def stop_spinning():
    global is_spinning
    is_spinning = False


# ---------------- BUTTON CONTROLS ----------------

def draw_controls():
    canvas.delete("controls")

    button_color = "#222222"
    text_color = "white"

    buttons = [
        ("⏮", 705, 185, "prev"),
        ("▶", 705, 250, "play"),
        ("⏭", 705, 315, "next"),
    ]

    for symbol, x, y, tag in buttons:
        canvas.create_oval(
            x - 28,
            y - 28,
            x + 28,
            y + 28,
            fill=button_color,
            outline="white",
            width=2,
            tags=("controls", tag)
        )

        canvas.create_text(
            x,
            y,
            text=symbol,
            fill=text_color,
            font=("Arial", 24, "bold"),
            tags=("controls", tag)
        )

    canvas.tag_bind("play", "<Button-1>", lambda e: run_in_thread(toggle_play_pause))
    canvas.tag_bind("next", "<Button-1>", lambda e: run_in_thread(skip_next))
    canvas.tag_bind("prev", "<Button-1>", lambda e: run_in_thread(skip_previous))


def update_play_button():
    canvas.delete("play")

    symbol = "▶" if is_paused else "⏸"

    x = 705
    y = 250

    canvas.create_oval(
        x - 28,
        y - 28,
        x + 28,
        y + 28,
        fill="#222222",
        outline="white",
        width=2,
        tags=("controls", "play")
    )

    canvas.create_text(
        x,
        y,
        text=symbol,
        fill="white",
        font=("Arial", 24, "bold"),
        tags=("controls", "play")
    )

    canvas.tag_bind("play", "<Button-1>", lambda e: run_in_thread(toggle_play_pause))


def draw_power_button():
    x = 95
    y = 420

    canvas.create_oval(
        x - 28,
        y - 28,
        x + 28,
        y + 28,
        fill="#8B0000",
        outline="white",
        width=2,
        tags=("power",)
    )

    canvas.create_text(
        x,
        y,
        text="⏻",
        fill="white",
        font=("Arial", 24, "bold"),
        tags=("power",)
    )

    canvas.tag_bind("power", "<Button-1>", lambda e: run_in_thread(shutdown_pi))


def toggle_play_pause():
    global is_paused

    if spotify_client is None or spotify_device_id is None:
        print("Spotify is not ready yet.")
        return

    try:
        if is_paused:
            spotify_client.start_playback(device_id=spotify_device_id)
            root.after(0, start_spinning)
            is_paused = False
        else:
            spotify_client.pause_playback(device_id=spotify_device_id)
            root.after(0, stop_spinning)
            is_paused = True

        root.after(0, update_play_button)

    except Exception as e:
        print(f"Play/pause error: {e}")


def skip_next():
    if spotify_client is None or spotify_device_id is None:
        print("Spotify is not ready yet.")
        return

    try:
        spotify_client.next_track(device_id=spotify_device_id)
        root.after(0, start_spinning)
        print("Skipped forward.")

    except Exception as e:
        print(f"Skip next error: {e}")


def skip_previous():
    if spotify_client is None or spotify_device_id is None:
        print("Spotify is not ready yet.")
        return

    try:
        spotify_client.previous_track(device_id=spotify_device_id)
        root.after(0, start_spinning)
        print("Skipped back.")

    except Exception as e:
        print(f"Skip previous error: {e}")


# ---------------- VOLUME CONTROLS ----------------

def set_volume(volume):
    global current_volume

    current_volume = max(0, min(100, volume))

    try:
        subprocess.run(
            ["amixer", "-c", "2", "sset", "Speaker", f"{current_volume}%"],
            check=False
        )

        subprocess.run(
            ["amixer", "-c", "2", "sset", "Headphone", f"{current_volume}%"],
            check=False
        )

        print(f"System volume set to {current_volume}%")
        root.after(0, draw_volume_controls)

    except Exception as e:
        print(f"Volume error: {e}")


def draw_volume_controls():
    canvas.delete("volume")

    x = 95
    y = 250

    canvas.create_oval(
        x - 28, y - 90,
        x + 28, y - 34,
        fill="#222222",
        outline="white",
        width=2,
        tags=("volume", "vol_up")
    )

    canvas.create_text(
        x, y - 62,
        text="+",
        fill="white",
        font=("Arial", 28, "bold"),
        tags=("volume", "vol_up")
    )

    canvas.create_text(
        x, y,
        text=f"{current_volume}%",
        fill="white",
        font=("Arial", 22, "bold"),
        tags="volume"
    )

    canvas.create_oval(
        x - 28, y + 34,
        x + 28, y + 90,
        fill="#222222",
        outline="white",
        width=2,
        tags=("volume", "vol_down")
    )

    canvas.create_text(
        x, y + 62,
        text="-",
        fill="white",
        font=("Arial", 28, "bold"),
        tags=("volume", "vol_down")
    )

    canvas.tag_bind("vol_up", "<Button-1>", lambda e: run_in_thread(lambda: set_volume(current_volume + 10)))
    canvas.tag_bind("vol_down", "<Button-1>", lambda e: run_in_thread(lambda: set_volume(current_volume - 10)))


# ---------------- SPOTIFY ART ----------------

def show_playlist_art(sp, spotify_uri):
    try:
        playlist_id = spotify_uri.split(":")[-1]
        images = sp.playlist_cover_image(playlist_id)

        if images:
            image_url = images[0]["url"]
            root.after(0, lambda: show_image_from_url(image_url))
            root.after(0, start_spinning)
        else:
            print("No playlist image found.")

    except Exception as e:
        print(f"Could not load playlist art: {e}")


# ---------------- ENV / LOGGING ----------------

load_dotenv()

logging.getLogger("mfrc522Logger").setLevel(logging.CRITICAL)

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise Exception("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET")

CACHE_PATH = "/home/djudson/.cache-spotify-token"


# ---------------- RFID TO PLAYLIST MAP ----------------

CARD_MAP = {
    712494431141: "YOUR PLAYLIST HERE",
    14524955170245: "YOUR PLAYLIST HERE",
}

SCAN_COOLDOWN_SECONDS = 8


# ---------------- SPOTIFY ----------------

def get_spotify_client():
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri="http://127.0.0.1:9090/callback",
        scope=(
            "user-read-playback-state "
            "user-modify-playback-state "
            "playlist-read-private"
        ),
        cache_path=CACHE_PATH,
        open_browser=False,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def get_raspotify_device_id(sp):
    try:
        devices = sp.devices().get("devices", [])
    except SpotifyException as e:
        print(f"Could not get Spotify devices: {e}")
        return None

    print("Available Spotify devices:")

    for device in devices:
        print(
            f"Name={device.get('name')} "
            f"ID={device.get('id')} "
            f"Active={device.get('is_active')}"
        )

    for device in devices:
        name = device.get("name", "").lower()

        if "pideck" in name or "raspotify" in name or "record-player" in name:
            print(f"Found Spotify device: {device.get('name')}")
            return device.get("id")

    print("No PiDeck/Raspotify device found.")
    return None


def ensure_raspotify_ready():
    global spotify_device_id

    if spotify_client is None:
        print("Spotify client is not ready.")
        return None

    device_id = get_raspotify_device_id(spotify_client)

    if not device_id:
        print("Raspotify is not available yet.")
        return None

    spotify_device_id = device_id

    try:
        print("Transferring playback to Raspberry Pi...")
        print(f"Using device ID: {device_id}")

        spotify_client.transfer_playback(
            device_id=device_id,
            force_play=True
        )

        sleep(1)

    except SpotifyException as e:
        print(f"Transfer playback error: {e}")
    except Exception as e:
        print(f"Unexpected transfer error: {e}")

    return device_id


def play_spotify_uri(sp, spotify_uri):
    global is_paused

    device_id = ensure_raspotify_ready()

    if not device_id:
        print("Could not play because Raspotify was not found.")
        return

    try:
        print("Starting playback with random playlist track...")

        playlist_id = spotify_uri.split(":")[-1]

        playlist = sp.playlist_items(
            playlist_id,
            fields="total",
            limit=1
        )

        total_tracks = playlist["total"]

        if total_tracks > 0:
            random_position = random.randint(0, total_tracks - 1)
        else:
            random_position = 0

        print(f"Starting at random playlist position: {random_position}")

        sp.shuffle(True, device_id=device_id)
        sleep(0.5)

        sp.start_playback(
            device_id=device_id,
            context_uri=spotify_uri,
            offset={"position": random_position}
        )

        is_paused = False
        root.after(0, update_play_button)
        root.after(0, start_spinning)

        print("Playback started with shuffle.")

    except SpotifyException as e:
        print(f"Spotify error: {e}")
        sleep(3)

    except Exception as e:
        print(f"Unexpected playback error: {e}")
        sleep(3)


# ---------------- RFID LOOP ----------------

def rfid_loop():
    global spotify_client

    print("Starting RFID Spotify Record Player...")

    reader = MFRC522()
    spotify_client = get_spotify_client()

    last_card_id = None

    print("Waiting for record scan...")

    try:
        while True:
            status, tag_type = reader.MFRC522_Request(reader.PICC_REQIDL)

            if status != reader.MI_OK:
                sleep(0.1)
                continue

            status, uid = reader.MFRC522_Anticoll()

            if status != reader.MI_OK:
                sleep(0.1)
                continue

            card_id = int("".join(str(x) for x in uid))

            print(f"Card Value is: {card_id}")

            if card_id == last_card_id:
                print("Same card scanned again. Ignoring.")
                sleep(SCAN_COOLDOWN_SECONDS)
                continue

            last_card_id = card_id

            spotify_uri = CARD_MAP.get(card_id)

            if spotify_uri:
                print(f"Playing: {spotify_uri}")

                play_spotify_uri(
                    spotify_client,
                    spotify_uri
                )

                show_playlist_art(
                    spotify_client,
                    spotify_uri
                )

            else:
                print("Unknown card.")
                print(card_id)

            sleep(SCAN_COOLDOWN_SECONDS)

    except Exception as e:
        print(f"RFID Loop Error: {e}")

    finally:
        GPIO.cleanup()


# ---------------- CLEAN EXIT ----------------

def close_app():
    print("Closing app...")

    stop_spinning()
    GPIO.cleanup()
    root.destroy()


# ---------------- START APP ----------------

if __name__ == "__main__":
    draw_controls()
    draw_volume_controls()
    draw_power_button()
    spin_record()

    thread = threading.Thread(
        target=rfid_loop,
        daemon=True
    )

    thread.start()

    root.protocol("WM_DELETE_WINDOW", close_app)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        close_app()
