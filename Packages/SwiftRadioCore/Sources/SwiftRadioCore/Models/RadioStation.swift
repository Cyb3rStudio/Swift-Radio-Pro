//
//  RadioStation.swift
//  SwiftRadioCore
//
//  Created by Fethi El Hassasna on 2026-09-07.
//
//  SPDX-License-Identifier: MIT
//  See LICENSE in the repository root.
//

import Foundation

/// A station's catalog data, stable identity, and metadata-independent presentation helpers.
public struct RadioStation: Codable, Hashable, Sendable, Identifiable {
    public var name: String
    public var streamURL: String
    public var imageURL: String
    public var desc: String
    public var longDesc: String
    public var website: String?
    public var country: String?
    public var countryCode: String?
    public var city: String?
    public var tags: String?
    public var id: String { streamURL + "|" + name }

    public init(name: String, website: String? = nil, streamURL: String, imageURL: String,
                desc: String, longDesc: String = "",
                country: String? = nil, countryCode: String? = nil,
                city: String? = nil, tags: String? = nil) {
        self.name = name
        self.website = website
        self.streamURL = streamURL
        self.imageURL = imageURL
        self.desc = desc
        self.longDesc = longDesc
        self.country = country
        self.countryCode = countryCode
        self.city = city
        self.tags = tags
    }

    private enum CodingKeys: String, CodingKey {
        case name, streamURL, imageURL, desc, longDesc, website
        case country, countryCode, city, tags
    }

    /// Hand-written so `longDesc` is genuinely optional in a fork's catalog: synthesized decoding
    /// requires the nonoptional property and the initializer's default never applies. A missing
    /// key becomes an empty string, which the station info screen already renders as its default.
    /// The location keys follow the same rule as `website`: absent in older catalogs, nil here.
    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        streamURL = try container.decode(String.self, forKey: .streamURL)
        imageURL = try container.decode(String.self, forKey: .imageURL)
        desc = try container.decode(String.self, forKey: .desc)
        longDesc = try container.decodeIfPresent(String.self, forKey: .longDesc) ?? ""
        website = try container.decodeIfPresent(String.self, forKey: .website)
        country = try container.decodeIfPresent(String.self, forKey: .country)
        countryCode = try container.decodeIfPresent(String.self, forKey: .countryCode)
        city = try container.decodeIfPresent(String.self, forKey: .city)
        tags = try container.decodeIfPresent(String.self, forKey: .tags)
    }

    public var hasValidWebsite: Bool {
        guard let website, let url = URL(string: website) else { return false }
        return Self.isWebURL(url)
    }

    public var shoutout: String {
        let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String
            ?? Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? ""
        return "I'm listening to \(name) via \(appName) app"
    }

    public func musicSearchURL(track: String?, artist: String?) -> URL? {
        guard let track, let artist else { return nil }
        var components = URLComponents()
        components.scheme = "https"
        components.host = "music.apple.com"
        components.path = "/search"
        components.queryItems = [URLQueryItem(name: "term", value: "\(track) \(artist)")]
        // A literal plus must survive servers that interpret '+' as form-encoded space.
        components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        return components.url
    }

    public var artworkSource: ArtworkSource {
        guard !imageURL.isEmpty else { return .placeholder }
        if let url = URL(string: imageURL), Self.isWebURL(url) { return .remote(url) }
        return .asset(imageURL)
    }

    /// "City, Country" for station rows and the info screen; nil when the catalog
    /// entry carries no usable location. Empty parts never render, so "Nashville"
    /// stays "Nashville" and a blank pair collapses to nil.
    public var locationCaption: String? {
        let parts = [city, country].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }

    private static func isWebURL(_ url: URL) -> Bool {
        ["http", "https"].contains(url.scheme?.lowercased() ?? "") && !(url.host ?? "").isEmpty
    }

    /// Full catalog comparison, separate from the legacy equality/hash contract below.
    /// `==` includes the location and tag fields: they render in rows and feed search,
    /// so a location-only edit must change equality the same way `website` does below.
    func hasSameCatalogContent(as other: Self) -> Bool {
        self == other && website == other.website
    }

    // Preserve legacy model equality extended with the location keys: website is still
    // deliberately ignored here (see hasSameCatalogContent). Catalog refreshes use
    // hasSameCatalogContent(as:) so website-only edits are still installed.
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.name == rhs.name && lhs.streamURL == rhs.streamURL && lhs.imageURL == rhs.imageURL
            && lhs.desc == rhs.desc && lhs.longDesc == rhs.longDesc
            && lhs.country == rhs.country && lhs.countryCode == rhs.countryCode
            && lhs.city == rhs.city && lhs.tags == rhs.tags
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(name)
        hasher.combine(streamURL)
        hasher.combine(imageURL)
        hasher.combine(desc)
        hasher.combine(longDesc)
        hasher.combine(country)
        hasher.combine(countryCode)
        hasher.combine(city)
        hasher.combine(tags)
    }
}
