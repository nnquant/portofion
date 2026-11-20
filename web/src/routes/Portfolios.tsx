import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorLine, Spark } from "../components/bits";
import { api, ws as wsPath } from "../lib/api";
import { useWs } from "../lib/ctx";
import { ago, pct, ratio, sign } from "../lib/format";
import type { PfListItem } from "../lib/types";

export function Portfolios() {
  const { slug: ws, canEdit } = useWs();
  const q = useQuery({ queryKey: ["portfolios", ws], queryFn: () => api<PfListItem[]>(wsPath(ws, "/portfolios")) });
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none items-center gap-2 border-b border-edge bg-surface px-3 py-1.5">
        {canEdit && (
          <Link className="btn btn-primary flex items-center" to="/w/$ws/p/$slug" params={{ ws, slug: "new" }} search={{ refs: undefined }}>
            New portfolio
          </Link>
        )}
        <div className="flex-1" />
        <span className="num text-muted">{q.data?.length ?? ""}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-surface">
        <ErrorLine error={q.error} />
        <table className="tbl">
          <thead>
            <tr>
              <th>Portfolio</th>
              <th>Name</th>
              <th>Scheme</th>
              <th>Rebalance</th>
              <th className="num">Members</th>
              <th />
              <th className="num">As of</th>
              <th className="num">AnnRet</th>
              <th className="num">Vol</th>
              <th className="num">Sharpe</th>
              <th className="num">MaxDD</th>
              <th className="num">Calmar</th>
              <th className="num">MTD</th>
              <th className="num">YTD</th>
              <th className="num">Updated</th>
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((r) => (
              <tr key={r.slug}>
                <td>
                  <Link to="/w/$ws/p/$slug" params={{ ws, slug: r.slug }} search={{ refs: undefined }} className="hover:text-accent hover:underline">
                    pf:{r.slug}
                  </Link>
                  {r.stale && <span className="ml-1.5 text-warn" title="stale">●</span>}
                  {r.status === "error" && <span className="ml-1.5 text-crit">error</span>}
                </td>
                <td className="text-ink2">{r.name !== r.slug ? r.name : ""}</td>
                <td>{r.scheme}</td>
                <td className="text-ink2">{r.scheme === "reported" ? "" : r.rebalance}</td>
                <td className="num">{r.n_members}</td>
                <td>
                  <Spark data={r.spark} />
                </td>
                <td className="num">{r.asof ?? "—"}</td>
                <td className={`num ${sign(r.metrics.ann_ret)}`}>{pct(r.metrics.ann_ret)}</td>
                <td className="num">{pct(r.metrics.ann_vol)}</td>
                <td className="num font-semibold">{ratio(r.metrics.sharpe)}</td>
                <td className="num">{pct(r.metrics.max_dd)}</td>
                <td className="num">{ratio(r.metrics.calmar)}</td>
                <td className={`num ${sign(r.metrics.ret_mtd)}`}>{pct(r.metrics.ret_mtd)}</td>
                <td className={`num ${sign(r.metrics.ret_ytd)}`}>{pct(r.metrics.ret_ytd)}</td>
                <td className="num text-ink2">{ago(r.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
