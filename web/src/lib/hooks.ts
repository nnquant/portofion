import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, qs, ws as wsPath } from "./api";
import { useWs } from "./ctx";
import type { SearchHit } from "./types";

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useSearchRefs(q: string) {
  const { slug } = useWs();
  const dq = useDebounced(q.trim(), 150);
  return useQuery({
    queryKey: ["search", slug, dq],
    queryFn: () => api<SearchHit[]>(wsPath(slug, `/search${qs({ q: dq })}`)),
    enabled: dq.length > 0,
    staleTime: 10_000,
  });
}

export function useLocalState<T>(key: string, initial: T | (() => T)): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    const init = () => (typeof initial === "function" ? (initial as () => T)() : initial);
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : init();
    } catch {
      return init();
    }
  });
  const set = (x: T) => {
    setV(x);
    try {
      localStorage.setItem(key, JSON.stringify(x));
    } catch {
      /* ignore */
    }
  };
  return [v, set];
}
