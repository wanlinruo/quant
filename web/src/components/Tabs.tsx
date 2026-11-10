import type { KeyboardEvent } from "react";

type Tab<K extends string> = { key: K; label: string };

type Props<K extends string> = { tabs: Tab<K>[]; value: K; onChange: (key: K) => void };

/** 面板标题栏右侧的小标签切换。 */
export function Tabs<K extends string>({ tabs, value, onChange }: Props<K>) {
  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number | undefined;
    if (e.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (e.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = tabs.length - 1;
    if (next === undefined) return;
    e.preventDefault();
    onChange(tabs[next].key);
    e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
  }

  return (
    <span className="tabs" role="tablist" aria-label="选项卡">
      {tabs.map((t, index) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={t.key === value}
          tabIndex={t.key === value ? 0 : -1}
          className={t.key === value ? "on" : undefined}
          onClick={() => onChange(t.key)}
          onKeyDown={(e) => onKeyDown(e, index)}
        >
          {t.label}
        </button>
      ))}
    </span>
  );
}
