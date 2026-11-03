//
//  StationsStoreTests.swift
//  SwiftRadioCoreTests
//
//  Created by Fethi El Hassasna on 2026-09-07.
//
//  SPDX-License-Identifier: MIT
//  See LICENSE in the repository root.
//

import Foundation
import Observation
import Testing
@testable import SwiftRadioCore

/// Covers loading, unchanged-list suppression, selection, search, and refresh removal.
@MainActor struct StationsStoreTests {
    @Test func loadAndSearch() async throws {
        let stations = try RadioStationTests.fixtures()
        let loader = FakeStationsLoader(stations: stations)
        let store = StationsStore(loader: loader, player: PlayerServiceTests.service())
        #expect(store.loadState == .idle)
        await store.load()
        #expect(store.loadState == .loaded)
        #expect(store.stations == stations)
        store.searchText = "rOcK"
        #expect(store.filteredStations.count == 3)
        store.searchText = " \n "
        #expect(store.filteredStations.count == 6)
        let before = store.stations
        let changes = ObservationCounter()
        withObservationTracking { _ = store.stations } onChange: { changes.increment() }
        await store.load()
        #expect(store.stations == before)
        #expect(changes.value == 0)
        #expect(await loader.loadCount == 2)
    }

    @Test func circularSelectionAndRemovalStopsAudio() async throws {
        let stations = try RadioStationTests.fixtures()
        let loader = FakeStationsLoader(stations: stations)
        let engine = FakeRadioPlayer()
        let player = PlayerServiceTests.service(engine)
        let store = StationsStore(loader: loader, player: player)
        await store.load()
        store.select(stations[5])
        store.selectNext()
        #expect(store.currentStation == stations[0])
        #expect(engine.radioURL == URL(string: stations[0].streamURL))
        store.selectPrevious()
        #expect(store.currentStation == stations[5])
        await loader.replace(with: Array(stations.dropLast()))
        await store.refresh()
        #expect(store.currentStation == nil)
        #expect(engine.calls.last == "stop")
        // Stopping alone leaves the removed stream loaded; a late remote Play would resume it.
        #expect(engine.radioURL == nil)
        let callsAfterRemoval = engine.calls.count
        player.play()
        #expect(engine.calls.count == callsAfterRemoval)
        #expect(player.state == .stopped)
    }

    @Test func bundleLoaderUsesFixtureBundle() async throws {
        let loader = BundleStationsLoader(fileName: "Fixtures/stations", bundle: .module)
        #expect(try await loader.load().count == 6)
    }

    @Test func emptyListSelectionIsSafe() async {
        let store = StationsStore(loader: FakeStationsLoader(stations: []), player: PlayerServiceTests.service())
        await store.load()
        store.selectNext()
        store.selectPrevious()
        #expect(store.currentStation == nil)
    }

    /// Country filtering, the picker's country list, and how they compose with search.
    @Test func countryFilterAndSearch() async throws {
        let stations = try RadioStationTests.fixtures()
        let store = StationsStore(loader: FakeStationsLoader(stations: stations),
                                  player: PlayerServiceTests.service())
        await store.load()

        // Localized-sorted catalog countries, no duplicates.
        #expect(store.countries == ["New Zealand", "Puerto Rico", "United States"])

        store.selectedCountry = "New Zealand"
        #expect(store.filteredStations.map(\.name) == ["The Rock FM"])

        // Filters compose: country first, then the query.
        store.searchText = "wellington" // city, matched across fields
        #expect(store.filteredStations.map(\.name) == ["The Rock FM"])
        store.searchText = "san juan"
        #expect(store.filteredStations.isEmpty)

        store.selectedCountry = nil
        store.searchText = " \n " // whitespace-only query shows everything
        #expect(store.filteredStations.count == 6)
        store.searchText = ""
        #expect(store.filteredStations.count == 6)
    }

    /// Search spans name, desc, city, country, and tags.
    @Test func searchSpansLocationFields() async throws {
        let stations = try RadioStationTests.fixtures()
        let store = StationsStore(loader: FakeStationsLoader(stations: stations),
                                  player: PlayerServiceTests.service())
        await store.load()

        store.searchText = "wellington" // city
        #expect(store.filteredStations.map(\.name) == ["The Rock FM"])
        store.searchText = "boulder" // city of Radio 1190, and its desc
        #expect(store.filteredStations.map(\.name) == ["Radio 1190"])
        store.searchText = "united states" // country
        #expect(store.filteredStations.count == 3)
        store.searchText = "college" // tags
        #expect(store.filteredStations.map(\.name) == ["Radio 1190"])
        store.searchText = "country" // tags of Absolute Country Hits
        #expect(store.filteredStations.map(\.name) == ["Absolute Country Hits"])
        store.searchText = "puerto rico"
        #expect(store.filteredStations.map(\.name) == ["AZ Rock Radio"])
    }

    /// The favorites-only view filters by the injected store and composes with the rest.
    @Test func favoritesOnlyFilter() async throws {
        let stations = try RadioStationTests.fixtures()
        let suite = "SwiftRadioCoreTests.StationsStore.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let favorites = FavoritesStore(defaults: defaults)
        let store = StationsStore(loader: FakeStationsLoader(stations: stations),
                                  player: PlayerServiceTests.service(), favorites: favorites)
        await store.load()

        #expect(store.filteredStations.count == 6) // default view unchanged
        favorites.toggle(stations[0])
        favorites.toggle(stations[2])
        store.showFavoritesOnly = true
        #expect(store.filteredStations.map(\.name) == ["Absolute Country Hits", "The Rock FM"])

        // Country filter composes with favorites.
        store.selectedCountry = "New Zealand"
        #expect(store.filteredStations.map(\.name) == ["The Rock FM"])
        store.selectedCountry = nil

        // Query composes with favorites; a non-matching one empties the list.
        store.searchText = "rOcK"
        #expect(store.filteredStations.map(\.name) == ["The Rock FM"])
        store.searchText = "jazz"
        #expect(store.filteredStations.isEmpty)

        // Untoggling removes it from the view; the favorites store is the single source.
        favorites.toggle(stations[2])
        #expect(store.filteredStations.map(\.name) == ["Absolute Country Hits"])
    }
}
