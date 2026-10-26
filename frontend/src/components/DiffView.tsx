export function DiffView({ diff }: { diff: string }) {
  if (!diff) return <p className="text-muted">No diff produced.</p>;
  return (
    <pre className="overflow-x-auto rounded border border-line bg-[#f6f8fa] py-2 font-mono text-[13px] leading-6">
      {diff.split("\n").map((ln, i) => {
        let cls = "text-ink";
        if (ln.startsWith("+++") || ln.startsWith("---")) cls = "text-muted font-semibold";
        else if (ln.startsWith("@@")) cls = "text-run bg-[#eaf1fb]";
        else if (ln.startsWith("+")) cls = "bg-[#e3f4e8] text-[#1d5a35]";
        else if (ln.startsWith("-")) cls = "bg-[#fbe9e7] text-[#8c2820]";
        return (
          <div key={i} className={`diff-line px-3 ${cls}`}>
            {ln || " "}
          </div>
        );
      })}
    </pre>
  );
}
