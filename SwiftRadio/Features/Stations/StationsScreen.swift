//
//  StationsScreen.swift
//  Swift Radio
//
//  Created by Fethi El Hassasna on 2026-09-07.
//
//  SPDX-License-Identifier: MIT
//  See LICENSE in the repository root.
//

import SwiftRadioCore
import SwiftUI

/// The station catalog: material cards with search, pull-to-refresh, About menu, and
/// a Now Playing toolbar indicator that opens the popup owned by RootView.
@MainActor struct StationsScreen: View {
    @Environment(StationsStore.self) private var stations
    @Environment(PlayerService.self) private var player
    @Binding var isPopupOpen: Bool
    @State private var showAbout = false

    var body: some View {
        catalog
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(GradientBackground())
            // The reference shows its loading cell only for an empty catalog; a search that
            // matches nothing leaves the list empty. The favorites filter gets its own message.
            .overlay {
                if stations.stations.isEmpty && stations.searchText.isEmpty {
                    EmptyStationsView()
                } else if stations.filteredStations.isEmpty
                            && stations.showFavoritesOnly && stations.searchText.isEmpty {
                    Text(Content.Stations.emptyFavoritesMessage)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .navigationTitle(Content.Stations.title)
            .navigationBarTitleDisplayMode(.large)
            .refreshable { await stations.refresh() }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showAbout = true } label: {
                        Image(systemName: "line.3.horizontal")
                    }
                    .accessibilityLabel(Content.About.title)
                }
                ToolbarItem(placement: .topBarLeading) {
                    CountryFilterMenu()
                }
                ToolbarItem(placement: .topBarTrailing) {
                    FavoritesToolbarToggle()
                }
                ToolbarItem(placement: .topBarTrailing) {
                    NowPlayingToolbarSlot { isPopupOpen = true }
                }
            }
            .sheet(isPresented: $showAbout) {
                AboutScreen()
            }
            .modifier(TrackHandoff())
    }

    /// Search filtering is only compiled in when `Config.searchable` is enabled.
    @ViewBuilder private var catalog: some View {
        if Config.searchable {
            stationList.searchable(text: Bindable(stations).searchText,
                                   placement: .navigationBarDrawer(displayMode: .automatic))
        } else {
            stationList
        }
    }

    private var stationList: some View {
        List(stations.filteredStations) { station in
            let isCurrent = station == stations.currentStation
            // Only the current row carries playback state, so a play/pause or buffering change
            // leaves every other row's inputs equal and SwiftUI skips their bodies.
            StationRow(
                station: station,
                isCurrent: isCurrent,
                isPlaying: isCurrent && player.state == .playing,
                isBuffering: isCurrent && player.isBuffering,
                isFavorite: stations.favorites.isFavorite(station)
            ) {
                tap(station)
            } favoriteAction: {
                stations.favorites.toggle(station)
            }
            .equatable()
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())
            .listRowSeparator(.hidden)
        }
    }

    /// Row tap follows the UIKit reference: a new station selects, the playing one opens the
    /// player, and any other state of the current one toggles playback.
    private func tap(_ station: SwiftRadioCore.RadioStation) {
        if stations.activate(station, on: .phone) == .alreadyActive {
            isPopupOpen = true
        }
    }
}

/// Shows the Now Playing indicator only while something plays or loads. Reading transport
/// state here keeps those changes from re-running the whole catalog body.
@MainActor private struct NowPlayingToolbarSlot: View {
    @Environment(StationsStore.self) private var stations
    @Environment(PlayerService.self) private var player
    let action: () -> Void

    var body: some View {
        if stations.currentStation != nil && (player.state == .playing || player.isBuffering) {
            NowPlayingIndicatorButton(action: action)
        }
    }
}

/// The country filter: a menu of every country present in the catalog, plus All Countries.
/// Isolated so reading `stations.countries` only re-runs this menu, not the catalog body.
@MainActor private struct CountryFilterMenu: View {
    @Environment(StationsStore.self) private var stations

    var body: some View {
        Menu {
            Picker(Content.Stations.countryFilter, selection: countryBinding) {
                Text(Content.Stations.allCountries).tag(String?.none)
                ForEach(stations.countries, id: \.self) { country in
                    Text(country).tag(String?.some(country))
                }
            }
        } label: {
            // A globe with the filter's state spelled out for VoiceOver; the plain
            // filter name would not say which country is active.
            Image(systemName: "globe")
        }
        .accessibilityLabel(Text("\(Content.Stations.countryFilter): \(activeCountry)"))
        .accessibilityIdentifier("countryFilter")
    }

    private var activeCountry: String {
        stations.selectedCountry ?? Content.Stations.allCountries
    }

    /// Picker over an optional needs an explicit two-way binding over the store.
    private var countryBinding: Binding<String?> {
        Binding(get: { stations.selectedCountry }, set: { stations.selectedCountry = $0 })
    }
}

/// Toolbar star: toggles the favorites-only view. Isolated so the star state change
/// only re-runs this toggle and the list, not the whole screen body.
@MainActor private struct FavoritesToolbarToggle: View {
    @Environment(StationsStore.self) private var stations

    var body: some View {
        Button {
            stations.showFavoritesOnly.toggle()
        } label: {
            Image(systemName: stations.showFavoritesOnly ? "star.fill" : "star")
        }
        .accessibilityLabel(stations.showFavoritesOnly
                            ? Content.Stations.showAllStations : Content.Stations.showFavorites)
        .accessibilityIdentifier("favoritesToggle")
    }
}

/// Advertises a web search for the playing track. Only this modifier reads track metadata, so
/// a metadata change does not re-run the catalog body.
@MainActor private struct TrackHandoff: ViewModifier {
    @Environment(PlayerService.self) private var player

    func body(content: Self.Content) -> some View {
        let url = HandoffActivity.searchURL(track: player.track, artist: player.artist)
        content.userActivity(NSUserActivityTypeBrowsingWeb, isActive: url != nil) { activity in
            activity.webpageURL = url
            activity.isEligibleForHandoff = true
        }
    }
}
