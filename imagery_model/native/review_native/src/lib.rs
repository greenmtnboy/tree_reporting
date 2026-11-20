//! Review-state acceleration. Unsupported Python values fail explicitly;
//! supported JSON bytes remain identical to the Python implementation.
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString};
use std::collections::{HashMap, HashSet};
mod ranking;

pyo3::create_exception!(_review_native, UnsupportedValue, PyTypeError);

struct Encoder {
    output: Vec<u8>,
    pretty: bool,
    ancestors: HashSet<usize>,
}

impl Encoder {
    fn line(&mut self, depth: usize) {
        if self.pretty {
            self.output.push(b'\n');
            self.output.resize(self.output.len() + 2 * depth, b' ');
        }
    }

    fn hex_escape(&mut self, n: u16) {
        const HEX: &[u8] = b"0123456789abcdef";
        self.output.extend_from_slice(b"\\u");
        for shift in [12, 8, 4, 0] {
            self.output.push(HEX[((n >> shift) & 15) as usize]);
        }
    }

    fn string(&mut self, s: &str) {
        self.output.push(b'"');
        for c in s.chars() {
            match c {
                '"' => self.output.extend_from_slice(b"\\\""),
                '\\' => self.output.extend_from_slice(b"\\\\"),
                '\n' => self.output.extend_from_slice(b"\\n"),
                '\r' => self.output.extend_from_slice(b"\\r"),
                '\t' => self.output.extend_from_slice(b"\\t"),
                '\u{08}' => self.output.extend_from_slice(b"\\b"),
                '\u{0c}' => self.output.extend_from_slice(b"\\f"),
                ' '..='~' => self.output.push(c as u8),
                _ => {
                    let mut pair = [0u16; 2];
                    for unit in c.encode_utf16(&mut pair) {
                        self.hex_escape(*unit);
                    }
                }
            }
        }
        self.output.push(b'"');
    }

    fn value(&mut self, value: &Bound<'_, PyAny>, depth: usize) -> PyResult<()> {
        if depth > 512 {
            return Err(UnsupportedValue::new_err(
                "Review JSON exceeds native nesting limit",
            ));
        }
        if value.is_none() {
            self.output.extend_from_slice(b"null");
        } else if value.is_instance_of::<PyBool>() {
            self.output.extend_from_slice(if value.extract::<bool>()? {
                b"true"
            } else {
                b"false"
            });
        } else if let Ok(s) = value.downcast::<PyString>() {
            // UTF-8 conversion rejects unpaired surrogates; validated annotation
            // data is Unicode. Unsupported strings fail explicitly otherwise.
            self.string(s.to_str()?);
        } else if value.is_exact_instance_of::<PyInt>() {
            self.output
                .extend_from_slice(value.str()?.to_str()?.as_bytes());
        } else if let Ok(n) = value.downcast::<PyFloat>() {
            let f = n.value();
            if !f.is_finite() {
                return Err(UnsupportedValue::new_err(
                    "Nonfinite value requires Python encoding",
                ));
            }
            // Preserve CPython's exact float spelling, including -0.0, .0,
            // exponent zero padding and rounding. No revision/hash changes.
            // json.dumps uses base-float formatting even for subclasses such
            // as numpy.float64 (whose own repr is "np.float64(...)").
            self.output
                .extend_from_slice(PyFloat::new(value.py(), f).repr()?.to_str()?.as_bytes());
        } else {
            let address = value.as_ptr() as usize;
            if !self.ancestors.insert(address) {
                return Err(PyValueError::new_err("Circular reference detected"));
            }
            if value.is_exact_instance_of::<PyDict>() {
                let dict = value.downcast::<PyDict>()?;
                let mut items = Vec::with_capacity(dict.len());
                for (key, child) in dict.iter() {
                    if !key.is_exact_instance_of::<PyString>() {
                        return Err(UnsupportedValue::new_err(
                            "Custom keys require Python encoding",
                        ));
                    }
                    let key = key.downcast::<PyString>().map_err(|_| {
                        UnsupportedValue::new_err("Non-string keys require Python encoding")
                    })?;
                    items.push((key.to_str()?.to_owned(), child));
                }
                items.sort_unstable_by(|a, b| a.0.cmp(&b.0));
                self.output.push(b'{');
                for (i, (key, child)) in items.iter().enumerate() {
                    if i > 0 {
                        self.output.push(b',');
                    }
                    self.line(depth + 1);
                    self.string(key);
                    self.output.push(b':');
                    if self.pretty {
                        self.output.push(b' ');
                    }
                    self.value(child, depth + 1)?;
                }
                if !items.is_empty() {
                    self.line(depth);
                }
                self.output.push(b'}');
            } else if value.is_exact_instance_of::<PyList>() {
                let list = value.downcast::<PyList>()?;
                self.output.push(b'[');
                for (i, child) in list.iter().enumerate() {
                    if i > 0 {
                        self.output.push(b',');
                    }
                    self.line(depth + 1);
                    self.value(&child, depth + 1)?;
                }
                if !list.is_empty() {
                    self.line(depth);
                }
                self.output.push(b']');
            } else {
                return Err(UnsupportedValue::new_err(
                    "Unsupported value in review JSON",
                ));
            }
            self.ancestors.remove(&address);
        }
        Ok(())
    }
}

// Clone only the plain container/immutable-scalar domain. Memoization preserves
// shared references and cycles exactly like copy.deepcopy. Subclasses and other
// objects fail explicitly so custom __deepcopy__ behavior is never skipped.
fn clone_value(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    memo: &mut HashMap<usize, Py<PyAny>>,
    depth: usize,
) -> PyResult<Py<PyAny>> {
    if depth > 512 {
        return Err(UnsupportedValue::new_err(
            "Copy exceeds native nesting limit",
        ));
    }
    if value.is_none()
        || value.is_exact_instance_of::<PyBool>()
        || value.is_exact_instance_of::<PyString>()
        || value.is_exact_instance_of::<PyInt>()
        || value.is_exact_instance_of::<PyFloat>()
    {
        return Ok(value.clone().unbind());
    }
    let address = value.as_ptr() as usize;
    if let Some(existing) = memo.get(&address) {
        return Ok(existing.clone_ref(py));
    }
    if value.is_exact_instance_of::<PyDict>() {
        let result = PyDict::new(py);
        memo.insert(address, result.clone().into_any().unbind());
        for (key, child) in value.downcast::<PyDict>()?.iter() {
            if !key.is_exact_instance_of::<PyString>() {
                return Err(UnsupportedValue::new_err("Copy requires plain string keys"));
            }
            result.set_item(key, clone_value(py, &child, memo, depth + 1)?)?;
        }
        Ok(result.into_any().unbind())
    } else if value.is_exact_instance_of::<PyList>() {
        let result = PyList::empty(py);
        memo.insert(address, result.clone().into_any().unbind());
        for child in value.downcast::<PyList>()?.iter() {
            result.append(clone_value(py, &child, memo, depth + 1)?)?;
        }
        Ok(result.into_any().unbind())
    } else {
        Err(UnsupportedValue::new_err(
            "Custom objects require Python deepcopy",
        ))
    }
}

#[pyfunction]
fn copy_json(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    clone_value(py, value, &mut HashMap::new(), 0)
}

/// Both modes match the current server: sorted keys, ensure_ascii=True;
/// compact separators for hashing, indent=2 plus final newline for storage.
#[pyfunction]
#[pyo3(signature = (value, pretty=false))]
fn encode_json<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    pretty: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let mut encoder = Encoder {
        output: Vec::new(),
        pretty,
        ancestors: HashSet::new(),
    };
    encoder.value(value, 0)?;
    if pretty {
        encoder.output.push(b'\n');
    }
    Ok(PyBytes::new(py, &encoder.output))
}

#[pymodule]
fn _review_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("API_VERSION", 2)?;
    module.add(
        "UnsupportedValue",
        module.py().get_type::<UnsupportedValue>(),
    )?;
    module.add_function(wrap_pyfunction!(encode_json, module)?)?;
    module.add_function(wrap_pyfunction!(copy_json, module)?)?;
    module.add_function(wrap_pyfunction!(ranking::match_chips, module)?)?;
    Ok(())
}
