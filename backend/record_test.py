import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000
DURATION = 4

print("Recording starts now. Say: Open Notepad")

audio = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32",
)

sd.wait()
sf.write("test_command.wav", audio, SAMPLE_RATE)

print("Saved test_command.wav")