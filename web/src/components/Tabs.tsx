type Tab<K extends string> = { key: K; label: string };

type Props<K extends string> = { tabs: Tab<K>[]; value: K; onChange: (key: K) => void };

/** 面板标题栏右侧的小标签切换。 */
export function Tabs<K extends string>({ tabs, value, onChange }: Props<K>) {
  return (
    <span className="tabs">
      {tabs.map((t) => (
        <button key={t.key} type="button" className={t.key === value ? "on" : undefined} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </span>
  );
}
