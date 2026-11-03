import { useEffect, useRef, useState, type CSSProperties } from "react";

/** 仅在摘要超出可用宽度时滚动；重复内容不进入辅助阅读。 */
export function MarketTicker({ text }: { text: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const message = useRef<HTMLSpanElement>(null);
  const [distance, setDistance] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const measure = () => {
      const width = message.current?.scrollWidth ?? 0;
      const space = viewport.current?.clientWidth ?? 0;
      setDistance(space > 0 && width > space ? width + 40 : 0);
    };
    const frame = requestAnimationFrame(measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (viewport.current) observer?.observe(viewport.current);
    if (message.current) observer?.observe(message.current);
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [text]);

  const style = {
    "--ticker-distance": `-${distance}px`,
    "--ticker-duration": `${Math.max(20, distance / 24)}s`,
  } as CSSProperties;

  return (
    <div className={`market-ticker${distance ? " is-overflowing" : ""}${paused ? " is-paused" : ""}`} role="region" aria-label="市场速览" style={style}>
      <span className="market-ticker-label">市场速览</span>
      <div className="market-ticker-viewport" ref={viewport} title={text || "市场摘要暂不可用"} tabIndex={distance ? 0 : undefined}>
        <div className="market-ticker-track">
          <span className="market-ticker-text" ref={message}>{text || "市场摘要暂不可用"}</span>
          {distance > 0 && <span className="market-ticker-copy" aria-hidden="true">{text}</span>}
        </div>
      </div>
      {distance > 0 && (
        <button type="button" className="market-ticker-control" aria-label={paused ? "继续滚动市场速览" : "暂停市场速览"} aria-pressed={paused} title={paused ? "继续滚动" : "暂停滚动"} onClick={() => setPaused(!paused)}>
          <svg width="11" height="11" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
            {paused ? <path d="M5 3l8 5-8 5z" /> : <path d="M4 3h3v10H4zm5 0h3v10H9z" />}
          </svg>
        </button>
      )}
    </div>
  );
}
