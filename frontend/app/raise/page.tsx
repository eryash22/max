"use client";

import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { useAuth } from "../../hooks/useAuth";

// Restructure consolidation pass, Part G/H — a single "Raise Request / Incident" entry
// point for Hospital TL / Unit Head. The Requirement and Incident backend domains are
// NOT merged (per instruction) — each tile below routes into the existing domain's
// existing create page (/requirements/new or /incidents/new), just pre-filling the
// type so the underlying workflow (Manager approval -> Inventory Manager -> Fulfillment
// for requirements; Manager -> Biomedical Engineer -> PM/Calibration/Service Visit/
// Escalation for incidents) is unchanged.
const REQUIREMENT_TYPES = [
  { itemType: "DEVICE_MODEL", label: "New Device", description: "Request a new device/asset for your branch." },
  { itemType: "CONSUMABLE", label: "Consumable", description: "Request consumable stock (e.g. sensors, tubing)." },
  { itemType: "SPARE_PART", label: "Spare Part", description: "Request a spare/replacement part." },
];

const INCIDENT_TYPES = [
  { problemCategory: "Device Issue", label: "Device Issue", description: "A device is faulty, damaged, or not working." },
  { problemCategory: "Maintenance Request", label: "Maintenance", description: "Routine or corrective maintenance is needed." },
  { problemCategory: "Calibration Request", label: "Calibration", description: "A device needs calibration." },
  { problemCategory: "Service Visit Request", label: "Service Visit", description: "Request an engineer/vendor visit." },
  { problemCategory: "Other Biomedical Issue", label: "Other Biomedical Issue", description: "Anything else biomedical-related." },
];

export default function RaiseRequestPage() {
  const { hasPermission } = useAuth();
  const canRequirement =
    hasPermission("requirement.create") || hasPermission("requirement.read") || hasPermission("requirement.approve");
  const canIncident = hasPermission("incident.create") || hasPermission("incident.read");

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Raise Request / Incident</h1>
          <p>
            One place to start — pick what you need. New devices/consumables/spare parts go through the Requirement →
            Approval → Fulfillment workflow; device problems go through the Incident → Biomedical Engineer workflow.
          </p>
        </div>
      </div>

      {canRequirement && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Requirement (new device / consumable / spare part)</h3>
          <div className="stat-grid">
            {REQUIREMENT_TYPES.map((t) => (
              <Link key={t.itemType} className="stat-tile-link" href={`/requirements/new?item_type=${t.itemType}`}>
                <div className="stat-tile">
                  <div className="stat-tile__label">{t.label}</div>
                  <p style={{ margin: "4px 0 0", fontSize: "0.85em" }}>{t.description}</p>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {canIncident && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Incident (device issue / maintenance / calibration / service)</h3>
          <div className="stat-grid">
            {INCIDENT_TYPES.map((t) => (
              <Link
                key={t.problemCategory}
                className="stat-tile-link"
                href={`/incidents/new?problem_category=${encodeURIComponent(t.problemCategory)}`}
              >
                <div className="stat-tile">
                  <div className="stat-tile__label">{t.label}</div>
                  <p style={{ margin: "4px 0 0", fontSize: "0.85em" }}>{t.description}</p>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {!canRequirement && !canIncident && (
        <div className="card">
          <p style={{ margin: 0 }}>You don&apos;t have permission to raise requirements or incidents.</p>
        </div>
      )}
    </ProtectedShell>
  );
}
