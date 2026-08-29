"use client";

import { useRef, useState } from "react";

import { formatDuration } from "@/components/run-dashboard-model";
import styles from "@/components/run-dashboard.module.css";

type RunEvidencePlayerProps = {
  src: string;
  title: string;
  safeAddress: string;
  badge: string;
};

export function RunEvidencePlayer({
  src,
  title,
  safeAddress,
  badge,
}: RunEvidencePlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [paused, setPaused] = useState(true);
  const [mediaError, setMediaError] = useState<string>();

  function syncDuration() {
    const nextDuration = videoRef.current?.duration;
    setDuration(Number.isFinite(nextDuration) ? (nextDuration ?? 0) : 0);
  }

  async function togglePlayback() {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    if (!video.paused) {
      video.pause();
      return;
    }
    try {
      await video.play();
    } catch {
      setMediaError("The browser recording could not begin playback.");
    }
  }

  function seek(nextTime: number) {
    const video = videoRef.current;
    if (!video || !Number.isFinite(nextTime)) {
      return;
    }
    video.currentTime = nextTime;
    setCurrentTime(nextTime);
  }

  return (
    <div className={styles.recording}>
      <div className={styles.browserFrame}>
        <div className={styles.browserToolbar}>
          <span className={styles.windowDots} aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className={styles.safeAddress}>{safeAddress}</span>
          <span className={styles.recordingBadge}>
            <span aria-hidden="true" />
            {badge}
          </span>
        </div>
        <div className={styles.videoViewport}>
          <video
            ref={videoRef}
            aria-label={title}
            controls
            controlsList="nodownload noremoteplayback"
            disablePictureInPicture
            preload="metadata"
            src={src}
            onDurationChange={syncDuration}
            onLoadedMetadata={syncDuration}
            onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
            onPlay={() => setPaused(false)}
            onPause={() => setPaused(true)}
            onEnded={() => setPaused(true)}
            onError={() =>
              setMediaError("The recorded artifact is unavailable or could not be decoded.")
            }
          />
          {mediaError ? (
            <div className={styles.mediaError} role="status">
              <strong>Recording unavailable</strong>
              <span>{mediaError}</span>
            </div>
          ) : null}
        </div>
      </div>
      <div className={styles.playbackBar}>
        <button
          type="button"
          className={styles.playButton}
          onClick={() => void togglePlayback()}
          aria-label={paused ? "Play browser recording" : "Pause browser recording"}
        >
          {paused ? <PlayIcon /> : <PauseIcon />}
        </button>
        <label className={styles.seekControl}>
          <span className={styles.srOnly}>Recording position</span>
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={Math.min(currentTime, duration || 0)}
            disabled={duration <= 0}
            onChange={(event) => seek(event.currentTarget.valueAsNumber)}
          />
        </label>
        <output className={styles.playbackTime} aria-live="off">
          {formatDuration(currentTime)} / {formatDuration(duration)}
        </output>
      </div>
    </div>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M5 3.5 12 8l-7 4.5z" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4.5 3.5h2.25v9H4.5zm4.75 0h2.25v9H9.25z" />
    </svg>
  );
}
