import ColorSync
import Darwin
import Foundation

private typealias MainConnection = @convention(c) () -> Int32
private typealias CopyDisplaySpaces = @convention(c) (Int32) -> Unmanaged<CFArray>?

private let spaceFunctions: (MainConnection, CopyDisplaySpaces)? = {
    guard let library = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY | RTLD_LOCAL),
          let connection = dlsym(library, "SLSMainConnectionID"),
          let copySpaces = dlsym(library, "SLSCopyManagedDisplaySpaces") else { return nil }
    return (unsafeBitCast(connection, to: MainConnection.self),
            unsafeBitCast(copySpaces, to: CopyDisplaySpaces.self))
}()

@_cdecl("spoke_active_space")
public func spokeActiveSpace(_ displayID: UInt32, _ kind: UnsafeMutablePointer<Int32>) -> UInt64 {
    guard let (connection, copySpaces) = spaceFunctions,
          let info = copySpaces(connection())?.takeRetainedValue() as? [[String: Any]],
          let displayUUID = CGDisplayCreateUUIDFromDisplayID(displayID)?.takeRetainedValue(),
          let identifier = CFUUIDCreateString(nil, displayUUID) as String? else { return 0 }
    let display = info.first { ($0["Display Identifier"] as? String)?.caseInsensitiveCompare(identifier) == .orderedSame }
        ?? (info.count == 1 && info[0]["Display Identifier"] as? String == "Main" ? info[0] : nil)
    guard let current = display?["Current Space"] as? [String: Any],
          let id = current["ManagedSpaceID"] as? NSNumber,
          let type = current["type"] as? NSNumber else { return 0 }
    kind.pointee = type.int32Value
    return id.uint64Value
}
