"""
A long piece of Resolve work behind the busy overlay, without freezing Buddy.

Resolve's scripting library keeps Python's lock for as long as each call
takes (core/resolve_reader.py says how that was measured), so a big job made
on the UI thread - an OTIO round trip, hundreds of clips turned off - stops
everything in Buddy until it's done: the overlay's spinner stands still and,
after 5 s, Windows greys the window out as "Not Responding". run() hands the
job to the Resolve reader's process instead (the page's ResolveCalls' shared
reader, a job named in core/resolve_reader_child.py JOBS) and waits for its
answer with the UI thread free, so the overlay keeps turning and the window
keeps answering. Where that process can't run (no interpreter for it,
external scripting off), the job is done here as it always was - never on a
thread: writes to Resolve from a second thread of this process are what
pages/project_setup/worker.py warns can crash it.

    run(host, calls, name, args, job, done, message)

job(): the same work, here (the fallback). done(result, error) on the UI
thread once the overlay is down: the job's answer, or the exception - from
the reader's process, a ReadError whose `kind` is the exception's class
there. A job the reader's process died in isn't run again here: it may have
got halfway, and twice would make two of whatever it makes. One whose
process never reached Resolve is - that process made nothing. That's every
first try after a quiet spell where nothing outside Resolve may connect (the
free Resolve on a Mac, external scripting off): the reader is tried again
every RETRY_AFTER_S, and ResolveCalls falls back the same way for a read.
"""


def run(host, calls, name, args, job, done, message):
    def finished(result, error):
        host.set_busy(False)
        done(result, error)

    def here():
        try:
            result, error = job(), None
        except Exception as exc:  # noqa: BLE001 - done() says what went wrong
            result, error = None, exc
        finished(result, error)

    def answered(result, error):
        # Ended, and the reader not available again at once: its process
        # never got as far as Resolve (resolve_reader.ResolveReader._ended).
        if getattr(error, "kind", "") == "Ended" and not calls.reader.available():
            here()
        else:
            finished(result, error)

    host.set_busy(True, message)
    try:
        sent = calls.reader.available() and calls.reader.ask(name, args, answered)
    except Exception as exc:  # noqa: BLE001 - a bug (args the pipe can't carry): said, the overlay down
        return finished(None, exc)
    if not sent:
        here()
