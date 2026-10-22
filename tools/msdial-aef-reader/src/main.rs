use std::env;
use std::error::Error;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;

struct Args {
    file: PathBuf,
    request: Request,
    max_points: usize,
}

enum Request {
    Single { index: usize },
    Window { start: usize, count: usize },
}

struct Point {
    x: f32,
    intensity: f32,
}

struct Peak {
    file_id: i32,
    point_count: i32,
    top: f32,
    left: f32,
    right: f32,
    points: Vec<Point>,
}

struct Spot {
    rt: f32,
    ri: f32,
    mz: f32,
    drift: f32,
    main_type: u8,
    peaks: Vec<Peak>,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = parse_args()?;
    let mut file = File::open(&args.file)?;
    let (version, pointers) = read_header(&mut file)?;
    match args.request {
        Request::Single { index } => {
            if index >= pointers.len() {
                return Err(format!("feature index {index} is outside the archive").into());
            }
            file.seek(SeekFrom::Start(pointers[index]))?;
            let spot = read_spot(&mut file, args.max_points)?;
            write_single_json(&version, pointers.len(), &spot);
        }
        Request::Window { start, count } => {
            if start >= pointers.len() {
                return Err(format!("window start {start} is outside the archive").into());
            }
            if count == 0 {
                return Err("window count must be greater than zero".into());
            }
            let end = (start + count).min(pointers.len());
            let mut spots = Vec::with_capacity(end - start);
            for index in start..end {
                file.seek(SeekFrom::Start(pointers[index]))?;
                spots.push((index, read_spot(&mut file, args.max_points)?));
            }
            write_window_json(&version, pointers.len(), start, count, &spots);
        }
    }
    Ok(())
}

fn parse_args() -> Result<Args, Box<dyn Error>> {
    let mut file = None;
    let mut index = None;
    let mut start = None;
    let mut count = None;
    let mut max_points = 900usize;
    let mut iter = env::args().skip(1);

    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--file" => file = iter.next().map(PathBuf::from),
            "--index" => index = iter.next().and_then(|value| value.parse::<usize>().ok()),
            "--start" => start = iter.next().and_then(|value| value.parse::<usize>().ok()),
            "--count" => count = iter.next().and_then(|value| value.parse::<usize>().ok()),
            "--max-points" => {
                max_points = iter
                    .next()
                    .and_then(|value| value.parse::<usize>().ok())
                    .ok_or("--max-points requires an integer value")?;
            }
            _ => return Err(format!("unknown argument: {arg}").into()),
        }
    }

    let request = match (index, start, count) {
        (Some(index), None, None) => Request::Single { index },
        (None, Some(start), Some(count)) => Request::Window { start, count },
        _ => return Err("provide either --index or both --start and --count".into()),
    };

    Ok(Args {
        file: file.ok_or("--file is required")?,
        request,
        max_points,
    })
}

fn read_header(file: &mut File) -> Result<(String, Vec<u64>), Box<dyn Error>> {
    let mut version_bytes = [0u8; 10];
    file.read_exact(&mut version_bytes)?;
    let version = String::from_utf8_lossy(&version_bytes)
        .trim_end_matches('\0')
        .to_string();
    if version != "CSS1" {
        return Err(format!("unsupported EIC archive version: {version}").into());
    }

    let feature_count = read_i32(file)?;
    if feature_count < 0 {
        return Err(format!("invalid feature count: {feature_count}").into());
    }

    let mut pointers = Vec::with_capacity(feature_count as usize);
    for _ in 0..feature_count {
        pointers.push(read_i64(file)? as u64);
    }
    Ok((version, pointers))
}

fn read_spot(file: &mut File, max_points: usize) -> Result<Spot, Box<dyn Error>> {
    let rt = read_f32(file)?;
    let ri = read_f32(file)?;
    let mz = read_f32(file)?;
    let drift = read_f32(file)?;
    let main_type = read_u8(file)?;
    let sample_count = read_i32(file)?;
    if sample_count < 0 {
        return Err(format!("invalid sample count: {sample_count}").into());
    }

    let mut peaks = Vec::with_capacity(sample_count as usize);
    for _ in 0..sample_count {
        peaks.push(read_peak(file, max_points)?);
    }

    Ok(Spot {
        rt,
        ri,
        mz,
        drift,
        main_type,
        peaks,
    })
}

fn read_peak(file: &mut File, max_points: usize) -> Result<Peak, Box<dyn Error>> {
    let file_id = read_i32(file)?;
    let point_count = read_i32(file)?;
    if point_count < 0 {
        return Err(format!("invalid point count: {point_count}").into());
    }
    let top = read_f32(file)?;
    let left = read_f32(file)?;
    let right = read_f32(file)?;

    let mut all_points = Vec::with_capacity(point_count as usize);
    for _ in 0..point_count {
        all_points.push(Point {
            x: read_f32(file)?,
            intensity: read_f32(file)?,
        });
    }

    let points = downsample(all_points, max_points);
    Ok(Peak {
        file_id,
        point_count,
        top,
        left,
        right,
        points,
    })
}

fn downsample(points: Vec<Point>, max_points: usize) -> Vec<Point> {
    if max_points == 0 || points.len() <= max_points {
        return points;
    }
    let step = ((points.len() + max_points - 1) / max_points).max(1);
    let mut sampled = Vec::new();
    let mut index = 0usize;
    while index < points.len() {
        sampled.push(Point {
            x: points[index].x,
            intensity: points[index].intensity,
        });
        index += step;
    }
    if let Some(last) = points.last() {
        if sampled
            .last()
            .map(|point| point.x != last.x || point.intensity != last.intensity)
            .unwrap_or(true)
        {
            sampled.push(Point {
                x: last.x,
                intensity: last.intensity,
            });
        }
    }
    sampled
}

fn write_single_json(version: &str, feature_count: usize, spot: &Spot) {
    print!(
        "{{\"version\":\"{}\",\"feature_count\":{},\"center\":{{\"rt\":{},\"ri\":{},\"mz\":{},\"drift\":{},\"main_type\":\"{}\"}},\"trace_count\":{},\"peaks\":[",
        escape_json(version),
        feature_count,
        spot.rt,
        spot.ri,
        spot.mz,
        spot.drift,
        chrom_type_label(spot.main_type),
        spot.peaks.len()
    );

    for (peak_index, peak) in spot.peaks.iter().enumerate() {
        if peak_index > 0 {
            print!(",");
        }
        print!(
            "{{\"file_id\":{},\"point_count\":{},\"returned_point_count\":{},\"top\":{},\"left\":{},\"right\":{},\"points\":[",
            peak.file_id,
            peak.point_count,
            peak.points.len(),
            peak.top,
            peak.left,
            peak.right
        );
        for (point_index, point) in peak.points.iter().enumerate() {
            if point_index > 0 {
                print!(",");
            }
            print!("{{\"x\":{},\"intensity\":{}}}", point.x, point.intensity);
        }
        print!("]}}");
    }
    println!("]}}");
}

fn write_window_json(
    version: &str,
    feature_count: usize,
    requested_start: usize,
    requested_count: usize,
    spots: &[(usize, Spot)],
) {
    print!(
        "{{\"version\":\"{}\",\"feature_count\":{},\"start\":{},\"requested_count\":{},\"returned_count\":{},\"features\":[",
        escape_json(version),
        feature_count,
        requested_start,
        requested_count,
        spots.len()
    );
    for (feature_position, (index, spot)) in spots.iter().enumerate() {
        if feature_position > 0 {
            print!(",");
        }
        print!("{{\"index\":{},\"spot\":", index);
        write_spot_json(version, feature_count, spot);
        print!("}}");
    }
    println!("]}}");
}

fn write_spot_json(version: &str, feature_count: usize, spot: &Spot) {
    print!(
        "{{\"version\":\"{}\",\"feature_count\":{},\"center\":{{\"rt\":{},\"ri\":{},\"mz\":{},\"drift\":{},\"main_type\":\"{}\"}},\"trace_count\":{},\"peaks\":[",
        escape_json(version),
        feature_count,
        spot.rt,
        spot.ri,
        spot.mz,
        spot.drift,
        chrom_type_label(spot.main_type),
        spot.peaks.len()
    );
    for (peak_index, peak) in spot.peaks.iter().enumerate() {
        if peak_index > 0 {
            print!(",");
        }
        write_peak_json(peak);
    }
    print!("]}}");
}

fn write_peak_json(peak: &Peak) {
    print!(
        "{{\"file_id\":{},\"point_count\":{},\"returned_point_count\":{},\"top\":{},\"left\":{},\"right\":{},\"points\":[",
        peak.file_id,
        peak.point_count,
        peak.points.len(),
        peak.top,
        peak.left,
        peak.right
    );
    for (point_index, point) in peak.points.iter().enumerate() {
        if point_index > 0 {
            print!(",");
        }
        print!("{{\"x\":{},\"intensity\":{}}}", point.x, point.intensity);
    }
    print!("]}}");
}

fn chrom_type_label(value: u8) -> &'static str {
    match value {
        0 => "rt",
        1 => "ri",
        2 => "drift",
        3 => "mz",
        _ => "unknown",
    }
}

fn escape_json(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn read_u8(file: &mut File) -> Result<u8, Box<dyn Error>> {
    let mut buf = [0u8; 1];
    file.read_exact(&mut buf)?;
    Ok(buf[0])
}

fn read_i32(file: &mut File) -> Result<i32, Box<dyn Error>> {
    let mut buf = [0u8; 4];
    file.read_exact(&mut buf)?;
    Ok(i32::from_le_bytes(buf))
}

fn read_i64(file: &mut File) -> Result<i64, Box<dyn Error>> {
    let mut buf = [0u8; 8];
    file.read_exact(&mut buf)?;
    Ok(i64::from_le_bytes(buf))
}

fn read_f32(file: &mut File) -> Result<f32, Box<dyn Error>> {
    let mut buf = [0u8; 4];
    file.read_exact(&mut buf)?;
    Ok(f32::from_le_bytes(buf))
}
