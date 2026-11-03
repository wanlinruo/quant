import { useEffect, useState } from "react";
import { etParts } from "../lib/time";

/** 按当前美东日期显示“今日”；页面跨午夜时更新，不依赖旧缓存文件的日期。 */
export function useETToday(): string {
  const [today, setToday] = useState(() => etParts(new Date().toISOString()).date);
  useEffect(() => {
    const timer = setInterval(() => setToday(etParts(new Date().toISOString()).date), 60_000);
    return () => clearInterval(timer);
  }, []);
  return today;
}
