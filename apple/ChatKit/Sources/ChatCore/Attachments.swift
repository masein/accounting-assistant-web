import Foundation
import ImageIO
import UniformTypeIdentifiers

/// What can go with a message, and in what form: the server keeps photos (JPEG, PNG, WebP), PDFs and spreadsheets
/// (CSV, TSV, Excel), checked by their bytes, 20 MB at most (app/api/transactions.py).
public enum Attachments {
    /// The types the server takes as they are.
    public static let acceptedMIMEs: Set<String> = [
        "image/jpeg", "image/png", "image/webp", "application/pdf", "text/csv", "text/tab-separated-values",
        "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]

    /// The files the file picker offers: PDFs, spreadsheets and photos.
    public static var pickerTypes: [UTType] {
        [.pdf, .commaSeparatedText, .tabSeparatedText, .spreadsheet, .image]
            + ["org.openxmlformats.spreadsheetml.sheet", "com.microsoft.excel.xls"].compactMap { UTType($0) }
    }

    /// A file's type from its name ("statement.csv" → text/csv).
    public static func mime(forFileName name: String) -> String {
        let ext = (name as NSString).pathExtension.lowercased()
        switch ext {
        case "csv": return "text/csv"
        case "tsv": return "text/tab-separated-values"
        case "xls": return "application/vnd.ms-excel"
        case "xlsx": return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        default: return UTType(filenameExtension: ext)?.preferredMIMEType ?? "application/octet-stream"
        }
    }

    /// A photo in a form the server doesn't read (HEIC from the camera, TIFF) goes as a JPEG, turned the right way
    /// up; everything else as it is.
    public static func prepare(_ data: Data, name: String, mime: String) -> (data: Data, name: String, mime: String) {
        let original = (data, name, mime)
        guard mime.hasPrefix("image/"), !acceptedMIMEs.contains(mime),
              let source = CGImageSourceCreateWithData(data as CFData, nil) else { return original }
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                        kCGImageSourceCreateThumbnailWithTransform: true,
                                        kCGImageSourceThumbnailMaxPixelSize: 4096]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return original }
        let out = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil)
        else { return original }
        CGImageDestinationAddImage(destination, image,
                                   [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return original }
        let base = (name as NSString).deletingPathExtension
        return (out as Data, (base.isEmpty ? "photo" : base) + ".jpg", "image/jpeg")
    }
}
