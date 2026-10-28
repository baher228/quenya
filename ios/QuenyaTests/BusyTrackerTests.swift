import XCTest
@testable import Quenya

@MainActor
final class BusyTrackerTests: XCTestCase {
    func testSuccessTracksBusyAndLeavesNoError() async {
        let tracker = BusyTracker()
        var busyDuringOperation = false

        let completed = await tracker.run("save") {
            busyDuringOperation = tracker.isBusy("save")
        }

        XCTAssertTrue(completed)
        XCTAssertTrue(busyDuringOperation)
        XCTAssertFalse(tracker.isBusy("save"))
        XCTAssertNil(tracker.error("save"))
    }

    func testFailureResetsBusyAndLaterSuccessClearsError() async {
        let tracker = BusyTracker()

        let failed = await tracker.run("save") { throw SampleFailure.failed }
        XCTAssertFalse(failed)
        XCTAssertFalse(tracker.isBusy("save"))
        XCTAssertEqual(tracker.error("save"), "Request failed")

        let retried = await tracker.run("save") {}
        XCTAssertTrue(retried)
        XCTAssertNil(tracker.error("save"))
    }

    func testDuplicateKeyIsRejectedWhileDifferentKeyCanRun() async {
        let tracker = BusyTracker()
        var continuation: CheckedContinuation<Void, Never>?
        let first = Task {
            await tracker.run("same") {
                await withCheckedContinuation { continuation = $0 }
            }
        }
        while continuation == nil { await Task.yield() }

        var secondInvoked = false
        let duplicate = await tracker.run("same") { secondInvoked = true }
        let other = await tracker.run("other") {}

        XCTAssertFalse(duplicate)
        XCTAssertFalse(secondInvoked)
        XCTAssertTrue(other)
        continuation?.resume()
        let firstCompleted = await first.value
        XCTAssertTrue(firstCompleted)
        XCTAssertFalse(tracker.isBusy("same"))
    }

    func testCancellationDoesNotStoreAnError() async {
        let tracker = BusyTracker()

        let completed = await tracker.run("cancel") { throw CancellationError() }

        XCTAssertFalse(completed)
        XCTAssertFalse(tracker.isBusy("cancel"))
        XCTAssertNil(tracker.error("cancel"))
    }
}

private enum SampleFailure: LocalizedError {
    case failed
    var errorDescription: String? { "Request failed" }
}
