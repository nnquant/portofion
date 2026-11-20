import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useSearchRefs } from "../lib/hooks";
import { useWs } from "../lib/ctx";
import type { SearchHit } from "../lib/types";

export function hitTarget(ws: string, h: SearchHit) {
  if (h.kind === "portfolio") return { to: "/w/$ws/p/$slug", params: { ws, slug: h.ref.slice(3) }, search: {} };
  const [sb, v] = h.ref.split("@");
  const [s, b] = sb.split("/");
  return { to: "/w/$ws/s/$slug", params: { ws, slug: s }, search: { b, v } };
}

export function Palette({ onClose }: { onClose: () => void }) {
  const { slug: ws } = useWs();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const hits = useSearchRefs(q);
  const list = hits.data ?? [];
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => setI(0), [q]);
  useEffect(() => {
    box.current?.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" });
  }, [i]);

  function go(h: SearchHit, compare = false) {
    onClose();
    if (compare) nav({ to: "/w/$ws/compare", params: { ws }, search: { refs: h.ref } });
    else nav(hitTarget(ws, h) as any);
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-center bg-black/25 pt-[12vh]" onMouseDown={onClose}>
      <div className="card h-fit w-[560px] overflow-hidden shadow-xl" onMouseDown={(e) => e.stopPropagation()}>
        <input
          autoFocus
          className="h-10 w-full border-0 border-b border-edge bg-transparent px-3 text-[14px] outline-none"
          placeholder="strategy, branch, version, portfolio"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") (e.preventDefault(), setI((x) => Math.min(x + 1, list.length - 1)));
            if (e.key === "ArrowUp") (e.preventDefault(), setI((x) => Math.max(x - 1, 0)));
            if (e.key === "Enter" && list[i]) go(list[i], e.shiftKey);
          }}
        />
        <div ref={box} className="max-h-[50vh] overflow-auto py-1">
          {list.map((h, k) => (
            <div
              key={`${h.kind}:${h.ref}`}
              data-i={k}
              className={`flex h-7 cursor-pointer items-center justify-between px-3 ${k === i ? "bg-sel" : ""}`}
              onMouseEnter={() => setI(k)}
              onClick={(e) => go(h, e.shiftKey)}
            >
              <span className="truncate">{h.kind === "portfolio" ? `pf:${h.ref.slice(3)}` : h.ref}</span>
              <span className="ml-3 text-[11px] text-muted">{h.kind}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
