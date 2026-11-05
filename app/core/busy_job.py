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
got halfway, and twice would make two of whatever it makes.
"""


def run(host, calls, name, args, job, done, message):
    def finished(result, error):
        host.set_busy(False)
        done(result, error)

    host.set_busy(True, message)
    if calls.reader.available() and calls.reader.ask(name, args, finished):
        return
    try:
        result, error = job(), None
    except Exception as exc:  # noqa: BLE001 - done() says what went wrong
        result, error = None, exc
    finished(result, error)
