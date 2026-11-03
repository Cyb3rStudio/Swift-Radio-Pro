//
//  FavoritesStore.swift
//  SwiftRadioCore
//
//  Created by Fethi El Hassasna on 2026-11-02.
//
//  SPDX-License-Identifier: MIT
//  See LICENSE in the repository root.
//

import Foundation
import Observation

/// Remembers which stations the user starred, by stable station id, across launches.
/// UserDefaults-backed: favorites are small, user-scoped, and needed at catalog render time.
@MainActor @Observable public final class FavoritesStore {
    /// Storage key in the injected defaults suite.
    private static let key = "favoriteStationIDs"
    private let defaults: UserDefaults
    private var ids: Set<String>

    /// A persistent store over the given suite. Pass a test suite with a volatile instance.
    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.ids = Self.read(from: defaults)
    }

    /// The favorited stations of a catalog, in catalog order.
    public func favoriteStations(in stations: [RadioStation]) -> [RadioStation] {
        let ids = self.ids
        return stations.filter { ids.contains($0.id) }
    }

    public func isFavorite(_ station: RadioStation) -> Bool {
        ids.contains(station.id)
    }

    /// Toggles membership and returns whether the station is favorited afterwards.
    @discardableResult public func toggle(_ station: RadioStation) -> Bool {
        if ids.insert(station.id).inserted == false {
            ids.remove(station.id)
            persist()
            return false
        }
        persist()
        return true
    }

    /// Rewrites the stored set; ids are small, so a full overwrite per toggle is fine.
    private func persist() {
        defaults.set(Array(ids), forKey: Self.key)
    }

    private static func read(from defaults: UserDefaults) -> Set<String> {
        Set(defaults.stringArray(forKey: key) ?? [])
    }
}