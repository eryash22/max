import type { ReactNode } from "react";

export interface Column<T> {
  header: ReactNode;
  render: (row: T) => ReactNode;
  key: string;
}

export function DataTable<T extends { id: string }>({ columns, rows, emptyLabel = "No records found." }: {
  columns: Column<T>[];
  rows: T[];
  emptyLabel?: string;
}) {
  if (rows.length === 0) {
    return <div className="state-panel">{emptyLabel}</div>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key}>{col.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((col) => (
                <td key={col.key}>{col.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
