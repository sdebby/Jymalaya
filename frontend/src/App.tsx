import { useEffect, useMemo, useState } from "react";
import { C } from "./theme";
import { useDashboardData } from "./useDashboardData";
import { isParticipating, visibleEvents } from "./derive";
import { DEFAULT_SETTINGS, loadSettings, loadWelcomeSeen, saveSettings, saveWelcomeSeen, type AppSettings } from "./settings";
import type { CoachAttachment } from "./types";
import { TopBar } from "./components/TopBar";
import { GarminErrorModal } from "./components/GarminErrorModal";
import { MfaModal } from "./components/MfaModal";
import { PlanSummaryModal } from "./components/PlanSummaryModal";
import { PlanRequestsModal } from "./components/PlanRequestsModal";
import { EventRow } from "./components/EventRow";
import { TrainingLoadAndKpis, RecoveryTrends, Disciplines, Distributions, PastWeek, PredictedWeek, LoadFocusRow } from "./components/Sections";
import { TrainingDays } from "./components/TrainingDays";
import { SeasonPlan, type SeasonCreateOpts } from "./components/SeasonPlan";
import { SeasonPlanModal } from "./components/SeasonPlanModal";
import { CoachChat } from "./components/CoachChat";

export default function App({ onLoaded }: { onLoaded: () => void }) {
  const { error, vm, workouts, events, primaryEvent, weather, status, plan, seasonPlan, syncing, mfaPending, syncPhase, syncError, garminFailure, clearGarminFailure, syncNotice, showNotice, coachSummary, clearCoachSummary, refresh, moveSession, startSync, replanning, startReplan, planRequestPrompt, answerPlanRequestPrompt } = useDashboardData();
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  // Cancel on the MFA modal hides it for this challenge only; a new challenge
  // (pending flag cleared then raised again) re-opens it.
  const [mfaDismissed, setMfaDismissed] = useState(false);
  // Planned workout attached to the coach chat via "Ask coach".
  const [coachAttachment, setCoachAttachment] = useState<CoachAttachment | null>(null);
  // The season plan's create modal: closed, or open on an event (or the first).
  const [seasonCreate, setSeasonCreate] = useState<SeasonCreateOpts | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [welcomeSeen, setWelcomeSeen] = useState(loadWelcomeSeen);
  useEffect(() => {
    if (!mfaPending) setMfaDismissed(false);
  }, [mfaPending]);

  // The first load is over, one way or the other: BackendGate's waiting screen
  // has been covering it and can go.
  const firstLoadDone = vm !== null || error !== null;
  useEffect(() => {
    if (firstLoadDone) onLoaded();
  }, [firstLoadDone, onLoaded]);

  // Race days are drawn only for events the athlete is racing.
  const racingEvents = useMemo(() => events.filter(isParticipating), [events]);

  // Fresh start: never synced and nothing stored. Shown once -- closing it or
  // opening Settings from it marks it seen (see loadWelcomeSeen).
  const freshStart = status !== null && !status.last_sync && workouts.length === 0;
  function dismissWelcome(openSettings: boolean) {
    saveWelcomeSeen();
    setWelcomeSeen(true);
    if (openSettings) setShowSettings(true);
  }

  function updateSettings(s: AppSettings) {
    setSettings(s);
    saveSettings(s);
  }

  // After a full data reset: localStorage is already cleared by the modal, so
  // just drop in-memory settings back to defaults and re-fetch the (now empty)
  // dashboard. A page reload also resets the coach chat to its greeting.
  function handleDataDeleted() {
    setSettings(DEFAULT_SETTINGS);
    window.location.reload();
  }

  if (!firstLoadDone) return null;

  return (
    <div style={{ minHeight: "100vh", background: C.pageBg, padding: 24 }}>
      <div style={{ maxWidth: 1480, margin: "0 auto", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 6, overflow: "hidden" }}>
        <TopBar status={status} syncing={syncing} syncPhase={syncPhase} onSync={startSync} replanning={replanning} onReplan={startReplan} settings={settings} onSettingsChange={updateSettings} onDataDeleted={handleDataDeleted} onRecalculated={refresh} onInstructionsSaved={(note) => { refresh(); if (note) showNotice(note); }} showSettings={showSettings} onShowSettings={setShowSettings} />
        {/* Only the events row hides long-finished events; the grid and the
            predicted week still need them for race days. It also keeps the
            calendar-only ones, which the grid and the week must not draw as races. */}
        <EventRow primary={primaryEvent} weather={weather} events={visibleEvents(events)} onChanged={refresh} onPlan={(eventId) => setSeasonCreate({ eventId })} seasonPlan={seasonPlan} />

        {error && (
          <div style={{ padding: "10px 16px", background: "#2a1416", color: C.red, fontSize: 12, borderBottom: `1px solid ${C.border}` }}>
            Couldn't reach the API ({error}). Check that the backend is running and that InfluxDB is up.
          </div>
        )}
        {syncError && (
          <div style={{ padding: "10px 16px", background: "#2a1416", color: C.red, fontSize: 12, borderBottom: `1px solid ${C.border}` }}>
            {syncError}
          </div>
        )}
        {syncNotice && (
          <div style={{ padding: "10px 16px", background: "#14231a", color: C.fit, fontSize: 12, borderBottom: `1px solid ${C.border}` }}>
            {syncNotice}
          </div>
        )}
        {freshStart && !welcomeSeen && (
          <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "8px 16px", background: "#121a26", color: C.form, fontSize: 12, borderBottom: `1px solid ${C.border}` }}>
            <div style={{ flex: 1 }}>Welcome. To get the most accurate analytics, please update your data in Settings.</div>
            <button
              onClick={() => dismissWelcome(true)}
              style={{ cursor: "pointer", fontSize: 12, fontWeight: 600, color: "#fff", background: C.form, border: "none", borderRadius: 4, padding: "6px 12px", whiteSpace: "nowrap" }}
            >
              Open settings
            </button>
            <button
              onClick={() => dismissWelcome(false)}
              title="Close"
              aria-label="Close"
              style={{ cursor: "pointer", fontSize: 15, lineHeight: 1, color: C.muted, background: "none", border: "none", padding: "0 4px" }}
            >
              ×
            </button>
          </div>
        )}
        {mfaPending && !mfaDismissed && <MfaModal onDismiss={() => setMfaDismissed(true)} />}
        {garminFailure && (
          <GarminErrorModal
            failure={garminFailure}
            onClose={clearGarminFailure}
            onRetry={() => {
              clearGarminFailure();
              void startSync();
            }}
          />
        )}
        {coachSummary && vm && (
          <PlanSummaryModal plan={coachSummary} workouts={workouts} seasonPlan={seasonPlan} primaryEvent={primaryEvent} tsb={vm.current.tsb} onClose={clearCoachSummary} />
        )}
        {seasonCreate && (
          <SeasonPlanModal
            events={racingEvents}
            initialEventId={seasonCreate.eventId}
            initialMode={seasonCreate.mode}
            convert={seasonCreate.convert}
            weekStart={settings.weekStart}
            onClose={() => setSeasonCreate(null)}
            onCreated={refresh}
          />
        )}
        {planRequestPrompt && (
          <PlanRequestsModal requests={planRequestPrompt.requests} onAnswer={answerPlanRequestPrompt} />
        )}

        <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10, background: "#0b0c10" }}>
          {/* Empty only when the first load failed; the banner above says why.
              A refresh (plan edit, event edit, recalculation) swaps the new data
              into the mounted panels, so the page does not jump and the chat
              keeps its scroll. */}
          {vm && (
            <>
              {/* Directly under the events row (and its notice strips). */}
              <SeasonPlan plan={seasonPlan} events={racingEvents} onCreate={(opts) => setSeasonCreate(opts ?? {})} onChanged={refresh} onAskCoach={setCoachAttachment} />
              <TrainingLoadAndKpis vm={vm} plan={plan} />
              <RecoveryTrends vm={vm} />
              <TrainingDays workouts={workouts} events={racingEvents} weekStart={settings.weekStart} />
              <Disciplines workouts={workouts} />
              <Distributions vm={vm} />
              <LoadFocusRow vm={vm} />
              <PastWeek workouts={workouts} onAskCoach={setCoachAttachment} />
              <PredictedWeek
                plan={plan}
                settings={settings}
                workouts={workouts}
                events={racingEvents}
                onAskCoach={setCoachAttachment}
                locked={syncing || replanning}
                onMoveSession={async (from, index, to) => {
                  await moveSession(from, index, to);
                  // Its (date, session_index) reference now names another session.
                  setCoachAttachment((a) => (a?.kind === "plan" && (a.date === from || a.date === to) ? null : a));
                }}
              />
              <CoachChat
                workouts={workouts}
                plan={plan}
                tsb={vm.current.tsb}
                attachment={coachAttachment}
                onClearAttachment={() => setCoachAttachment(null)}
                onPlanUpdated={refresh}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
