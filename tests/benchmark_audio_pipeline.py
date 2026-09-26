"""Synthetic CPU-only microbenchmark. No devices, network or model loading.

Run: python3 -m tests.benchmark_audio_pipeline
"""
import asyncio
import platform
import statistics
import time
import timeit

from core.audio_pipeline import AudioFrame, AudioPipeline, AudioPolicy, InputState


def run(iterations=200_000, repetitions=5):
    pcm = bytes(2048)
    pipeline = AudioPipeline(policy=AudioPolicy(idle_after_seconds=0))
    queue = asyncio.Queue(maxsize=32)
    pipeline.attach(queue)
    pipeline.start_stream("local", "stream", "external-headset")
    rejected = AudioPipeline(state=lambda: InputState(muted=True))
    rejected.attach(asyncio.Queue(maxsize=32))
    rejected.start_stream("local", "stream", "external-headset")

    def construct():
        return AudioFrame("local", "external-headset", "stream", time.monotonic(), 16000, 1, pcm)

    def accept():
        pipeline.submit(construct())
        pipeline.ready_to_send(queue.get_nowait())

    def reject():
        rejected.submit(construct())

    print(f"Python {platform.python_version()} | {platform.machine()} | {platform.platform()}")
    print(f"{iterations:,} iterations x {repetitions} repetitions per case")
    for label, operation in (("construct", construct), ("accept + dequeue + final check", accept),
                             ("reject muted", reject)):
        timings = [seconds / iterations * 1e6 for seconds in
                   timeit.repeat(operation, number=iterations, repeat=repetitions)]
        print(f"{label}: median={statistics.median(timings):.3f} us/frame; "
              f"min={min(timings):.3f}; max={max(timings):.3f}")
    print("Accept/reject include frame construction; level meter is a constant double.")
    print("Excludes sounddevice, real PCM RMS, threads, Gemini and real network latency.")


if __name__ == "__main__":
    run()
