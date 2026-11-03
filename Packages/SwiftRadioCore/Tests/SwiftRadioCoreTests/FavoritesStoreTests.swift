//
//  FavoritesStoreTests.swift
//  SwiftRadioCoreTests
//
//  Created by Fethi El Hassasna on 2026-11-02.
//
//  SPDX-License-Identifier: MIT
//  See LICENSE in the repository root.
//

import Foundation
import Testing
@testable import SwiftRadioCore

/// Covers toggling, catalog ordering, and persistence across store recreation.
@MainActor struct FavoritesStoreTests {
    /// A volatile suite per test: no cross-test or cross-run favorite leakage.
    private func makeDefaults() throws -> (UserDefaults, cleanup: () -> Void) {
        let name = "SwiftRadioCoreTests.FavoritesStore.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: name))
        return (defaults, { defaults.removePersistentDomain(forName: name) })
    }

    private static func station(_ name: String) -> RadioStation {
        RadioStation(name: name, streamURL: "https://radio.example/\(name)",
                     imageURL: "", desc: "Radio", country: "France", city: "Paris")
    }

    @Test func toggleReturnsAndStoresMembership() throws {
        let (defaults, cleanup) = try makeDefaults()
        defer { cleanup() }
        let station = Self.station("Test")
        let favorites = FavoritesStore(defaults: defaults)

        #expect(!favorites.isFavorite(station))
        #expect(favorites.toggle(station) == true) // returns the new membership
        #expect(favorites.isFavorite(station))
        #expect(favorites.toggle(station) == false)
        #expect(!favorites.isFavorite(station))
    }

    @Test func favoritesSurviveStoreRecreation() throws {
        let (defaults, cleanup) = try makeDefaults()
        defer { cleanup() }
        let station = Self.station("Persistent")
        FavoritesStore(defaults: defaults).toggle(station)

        // A fresh store reads the persisted ids, as a relaunch would.
        let relaunched = FavoritesStore(defaults: defaults)
        #expect(relaunched.isFavorite(station))
    }

    @Test func favoriteStationsFollowCatalogOrder() throws {
        let (defaults, cleanup) = try makeDefaults()
        defer { cleanup() }
        let first = Self.station("First")
        let middle = Self.station("Middle")
        let last = Self.station("Last")
        let catalog = [first, middle, last]
        let favorites = FavoritesStore(defaults: defaults)

        favorites.toggle(last)
        favorites.toggle(first)
        #expect(favorites.favoriteStations(in: catalog) == [first, last])
        #expect(favorites.favoriteStations(in: Array(catalog.dropLast())) == [first])
        #expect(favorites.favoriteStations(in: []) == [])

        // A same-stream rename is a distinct id; ids are streamURL + name.
        var renamed = first
        renamed.name = "Renamed"
        #expect(!favorites.isFavorite(renamed))
    }
}