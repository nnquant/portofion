import { useState } from "react";
import { useSearchRefs } from "../lib/hooks";
import type { SearchHit } from "../lib/types";

export function RefPicker({ onPick, placeholder = "Add…", exclude = [], width = 220 }: { onPick: (ref: string, hit: SearchHit) => void; placeholder?: string; exclude?: string[]; width?: number }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  const hits = (useSearchRefs(q).data ?? []).filter((h) => !exclude.includes(h.ref));
  const pick = (h: SearchHit) => {
    onPick(h.ref, h);
    setQ("");
    setI(0);
  };
  return (
    <div className="relative" style={{ width }}>
      <input
        className="ctl w-full"
        placeholder={placeholder}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setI(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") (e.preventDefault(), setI((x) => Math.min(x + 1, hits.length - 1)));
          if (e.key === "ArrowUp") (e.preventDefault(), setI((x) => Math.max(x - 1, 0)));
          if (e.key === "Enter" && hits[i]) pick(hits[i]);
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q && hits.length > 0 && (
        <div className="card absolute left-0 top-[27px] z-40 max-h-[300px] w-[360px] overflow-auto py-1 shadow-lg">
          {hits.map((h, k) => (
            <div
              key={`${h.kind}:${h.ref}`}
              className={`flex h-6 cursor-pointer items-center justify-between px-2 ${k === i ? "bg-sel" : ""}`}
              onMouseDown={(e) => (e.preventDefault(), pick(h))}
              onMouseEnter={() => setI(k)}
            >
              <span className="truncate">{h.ref}</span>
              <span className="ml-2 text-[11px] text-muted">{h.kind}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
