import { ChevronLeft, ChevronRight, SkipForward } from "lucide-react";
import type { PlayerTrace } from "../types";
import { cx, roleKeyFromFrame, titleize } from "../utils";

interface Props {
  player: PlayerTrace | null;
  frameIndex: number;
  onFrameIndex: (index: number) => void;
}

export function Player({ player, frameIndex, onFrameIndex }: Props) {
  const frames = player?.frames || [];
  const frame = frames[frameIndex];
  const max = Math.max(frames.length - 1, 0);

  const nextRole = () => {
    const currentRole = roleKeyFromFrame(frame);
    const found = frames.find((candidate, index) => index > frameIndex && roleKeyFromFrame(candidate) !== currentRole);
    if (found) onFrameIndex(found.index);
  };

  return (
    <section className="player-panel">
      <div className="player-main">
        <button type="button" className="icon-button" onClick={() => onFrameIndex(Math.max(0, frameIndex - 1))} disabled={!frames.length || frameIndex <= 0} title="Previous frame">
          <ChevronLeft size={18} />
        </button>
        <input
          type="range"
          min={0}
          max={max}
          value={Math.min(frameIndex, max)}
          onChange={(event) => onFrameIndex(Number(event.target.value))}
          disabled={!frames.length}
        />
        <button type="button" className="icon-button" onClick={() => onFrameIndex(Math.min(max, frameIndex + 1))} disabled={!frames.length || frameIndex >= max} title="Next frame">
          <ChevronRight size={18} />
        </button>
        <button type="button" className="text-button" onClick={nextRole} disabled={!frames.length} title="Jump to next role">
          <SkipForward size={16} />
          Next Role
        </button>
      </div>
      <div className="role-progress" aria-hidden="true">
        {frames.map((item) => (
          <button
            type="button"
            key={`progress-${item.frame_id}`}
            className={cx("role-segment", roleKeyFromFrame(item), item.index === frameIndex && "active")}
            style={{ flexGrow: 1 }}
            onClick={() => onFrameIndex(item.index)}
            title={`${item.index}: ${item.name}`}
          />
        ))}
      </div>
      <div className="marker-rail">
        {frames.slice(0, 180).map((item) => (
          <button
            type="button"
            key={item.frame_id}
            className={cx("marker", item.index === frameIndex && "active", item.is_marker_candidate && "major", roleKeyFromFrame(item))}
            onClick={() => onFrameIndex(item.index)}
            title={`${item.index}: ${item.name}`}
          />
        ))}
      </div>
      <div className="frame-caption">
        <strong>{frames.length ? `${frameIndex + 1}/${frames.length}` : "0/0"}</strong>
        <span>{frame ? titleize(frame.name || frame.action_kind || "Frame") : "No player frames"}</span>
        <small>{frame?.summary || "No state-machine frame is available for this experiment."}</small>
      </div>
    </section>
  );
}
