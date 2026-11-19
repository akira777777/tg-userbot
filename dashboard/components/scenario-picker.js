"use client";

import { useMemo, useRef } from "react";
import { SCENARIO_GROUPS, SCENARIOS, scenarioFor } from "../lib/command-scenarios";
import { hapticImpact } from "../lib/telegram";

/**
 * Плитки сценариев. Клавиатура: стрелки переключают, Home/End — крайние.
 * Роли radio/radiogroup держат скринридер в курсе, какой сценарий выбран.
 */
export default function ScenarioPicker({ kind, onChange }) {
  const refs = useRef(new Map());

  const grouped = useMemo(
    () => SCENARIO_GROUPS.map((group) => ({
      ...group,
      items: SCENARIOS.filter((item) => item.group === group.id),
    })).filter((group) => group.items.length),
    [],
  );

  function onKeyDown(event, index) {
    const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let nextIndex = null;
    if (event.key in keys) nextIndex = (index + keys[event.key] + SCENARIOS.length) % SCENARIOS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = SCENARIOS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = SCENARIOS[nextIndex];
    onChange(next.id);
    refs.current.get(next.id)?.focus();
  }

  const activeGroup = scenarioFor(kind)?.group;

  return (
    <div className="scenario-flow" role="radiogroup" aria-label="Что сделать">
      {grouped.map((group) => (
        <div className="scenario-run" key={group.id}>
          <button
            type="button"
            className={`scenario-group-title${activeGroup === group.id ? " active" : ""}`}
            aria-hidden="true"
            tabIndex={-1}
            onClick={() => onChange(group.items[0].id)}
          >
            {group.title}
          </button>
          <div className="scenarios" data-group={group.id}>
          {group.items.map((item) => {
            const index = SCENARIOS.findIndex((entry) => entry.id === item.id);
            const active = kind === item.id;
            const classes = ["scenario", `scenario-${item.group}`];
            if (item.id === "STOP") classes.push("scenario-stop");
            if (active) classes.push("active");
            return (
              <button
                key={item.id}
                ref={(node) => {
                  if (node) refs.current.set(item.id, node);
                  else refs.current.delete(item.id);
                }}
                type="button"
                role="radio"
                aria-checked={active}
                aria-label={`${group.title}: ${item.label}`}
                tabIndex={active ? 0 : -1}
                title={item.hint}
                className={classes.join(" ")}
                onClick={() => {
                  if (!active) hapticImpact("light");
                  onChange(item.id);
                }}
                onKeyDown={(event) => onKeyDown(event, index)}
              >
                <span className="scenario-icon" aria-hidden="true">{item.icon}</span>
                <span className="scenario-label">{item.label}</span>
              </button>
            );
          })}
          </div>
        </div>
      ))}
    </div>
  );
}