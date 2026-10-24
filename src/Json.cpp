#include "Json.h"
#include "Util.h"
#include <vector>
#include <algorithm>

bool Json::load_json(const char *path_json)noexcept{

	std::string buf;
	if(!LoadFile(buf, path_json)){
		return false;
	}

	auto p = const_cast<char *>(buf.c_str());
	auto end = p + buf.size();
	size_t line = 1;
	size_t col  = 1;

	auto skip_ws = [&]()noexcept -> void {
		for(; p < end && std::isspace(static_cast<uint8_t>(*p)); ++p){
			if(*p == '\n'){
				++line;
				col = 1;
			}else{
				++col;
			}
		}
	};

	// 文字列パース
	auto parse_string = [&](std::string &value)noexcept -> bool {
		++p;
		auto start = p, d = p;
		while(p < end){
			char c = *p;
			if(c == '"'){
				++p;
				++col;
				*d = '\0';
				try{
					value = start;
				}catch(...){
					return false;
				}
				return true;
			}
			else if(c == '\n'){
				*(d++) = '\n';
				++p;
				++line;
				col = 1;
			}
			else if(c == '\\'){
				++p;
				++col;
				if(p >= end){
					//Error::StringFormat;
					return false;
				}
				c = *p;
				switch(c){
				case '"': *(d++) = '"'; ++p; ++col; break;
				case '\\': *(d++) = '\\'; ++p; ++col; break;
				case '/': *(d++) = '/'; ++p; ++col; break;
				case 'b': *(d++) = '\b'; ++p; ++col; break;
				case 'f': *(d++) = '\f'; ++p; ++col; break;
				case 'n': *(d++) = '\n'; ++p; ++col; break;
				case 'r': *(d++) = '\r'; ++p; ++col; break;
				case 't': *(d++) = '\t'; ++p; ++col; break;
				case 'u':
					{
						if(end - p < 4){
							//"invalid unicode escape"
							return false;
						}
						uint16_t code = 0;
						for(int i = 0; i < 4; ++i){
							char h = *p;
							++p;
							++col;
							code <<= 4;
							if(h >= '0' && h <= '9')code |= h - '0';
							else if(h >= 'a' && h <= 'f')code |= h - 'a' + 10;
							else if(h >= 'A' && h <= 'F')code |= h - 'A' + 10;
							else{
								//invalid hex
								return false;
							}
						}
						if(code < 0x80){
							*(d++) = static_cast<char>(code);
						}else if(code < 0x800){
							*(d++) = static_cast<char>(0xC0 | (code >> 6));
							*(d++) = static_cast<char>(0x80 | (code & 0x3F));
						}else{
							*(d++) = static_cast<char>(0xE0 | (code >> 12));
							*(d++) = static_cast<char>(0x80 | ((code >> 6) & 0x3F));
							*(d++) = static_cast<char>(0x80 | (code & 0x3F));
						}
					}
					break;
				default:
					return false;
					//return Error::StringFormat;
				}
			}
			else{
				*(d++) = c;
				++p;
				++col;
			}
		}
		//Error::Incomplete;
		return false;
	};

	auto parse_value = [&](auto &&self, Node &node)noexcept -> bool {
		skip_ws();
		if(p >= end){
			//make_error(err, p, "unexpected end");
			return false;
		}
		char c = *p;

		// Object
		if(c == '{'){
			Node::object_t object;
			++p;
			++col;
			skip_ws();

			if(p < end && *p == '}'){
				++p;
				++col;
				node = Node(std::move(object));
				return true;
			}

			while(true){
				Node::string_t key;
				if(p >= end || *p != '"'){
					return false;//Error::InvalidSyntax;
				}
				if(!parse_string(key)){
					return false;
				}
				skip_ws();
				if(p >= end || *p != ':'){
					return false;//Error::InvalidSyntax;
				}
				++p;
				++col;
				Node child;
				if(!self(self, child)){
					return false;
				}
				try{
					object.emplace(std::move(key), std::move(child));
				}catch(...){
					// Error::Incomplete;
					return false;
				}
				skip_ws();
				if(p >= end){
					//Error::Incomplete;
					return false;
				}
				if(*p == '}'){
					++p;
					++col;
					break;
				}
				if(*p != ','){
					//Error::InvalidSyntax;
					return false;
				}
				++p;
				++col;
				skip_ws();
			}

			node = Node(std::move(object));
			return true;
		}

		// Array
		else if(c == '['){
			Node::array_t array;
			++p;
			++col;
			skip_ws();

			if(p < end && *p == ']'){
				++p;
				++col;
				node = Node(std::move(array));
				return true;
			}

			while(true){
				Node child;
				if(!self(self, child)){
					return false;
				}
				try{
					array.push_back(std::move(child));
				}catch(...){
					// Error::Incomplete;
					return false;
				}
				skip_ws();
				if(p >= end){
					//Error::Incomplete;
					return false;
				}
				if(*p == ']'){
					++p;
					++col;
					break;
				}
				if(*p != ','){
					//Error::InvalidSyntax;
					return false;
				}
				++p;
				++col;
			}

			node = Node(std::move(array));
			return true;
		}

		// String
		else if(c == '"'){
			Node::string_t string;
			if(!parse_string(string)){
				return false;
			}
			node = Node(std::move(string));
			return true;
		}

		// Boolean
		else if(c == 't' && end - p >= 4 && memcmp(p, "true", 4) == 0){
			p += 4;
			col += 4;
			node = Node(true);
			return true;
		}
		else if (c == 'f' && end - p >= 5 && memcmp(p, "false", 5) == 0){
			p += 5;
			col += 5;
			node = Node(false);
			return true;
		}

		// Null
		else if (c == 'n' && end - p >= 4 && memcmp(p, "null", 4) == 0){
			p += 4;
			col += 4;
			node = Node();
			return true;
		}

		// Number
		else if (c == '-' || std::isdigit(static_cast<uint8_t>(c))){
			const char *start = p;
			bool is_float = false;

			// 符号
			if(*p == '-'){
				++p;
			}

			// 整数部
			if(p >= end){
				return false;
			}
			if(*p == '0'){
				++p;
				if(p < end && std::isdigit(static_cast<uint8_t>(*p))){
					return false; // 01, 00
				}
			}else if (std::isdigit(static_cast<uint8_t>(*p))){
				while(p < end && std::isdigit(static_cast<uint8_t>(*p))){
					++p;
				}
			}else{
				return false;
			}

			// 小数部
			if(p < end && *p == '.'){
				is_float = true;
				++p;
				if(p >= end || !std::isdigit(static_cast<uint8_t>(*p))){
					return false;
				}
				while(p < end && std::isdigit(static_cast<uint8_t>(*p))){
					++p;
				}
			}

			// 指数部
			if(p < end && (*p == 'e' || *p == 'E')){
				is_float = true;
				++p;
				if(p < end && (*p == '+' || *p == '-')){
					++p;
				}
				if(p >= end || !std::isdigit(static_cast<uint8_t>(*p))){
					return false;
				}
				while(p < end && std::isdigit(static_cast<uint8_t>(*p))){
					++p;
				}
			}

			char *endp = nullptr;
			if(is_float){
				auto v = strtod(start, &endp);
				if(endp != p){
					return false;
				}
				node = Node(static_cast<Node::float64_t>(v));
			}else{
				auto v = strtoll(start, &endp, 10);
				if(endp != p){
					return false;
				}
				node = Node(static_cast<Node::int64_t>(v));
			}

			col += p - start;
			return true;
		}

		//make_error(err, p, "invalid value");
		return false;
	};

	skip_ws();
	if(p >= end){
		return false;
		//return Error::Incomplete;
	}

  return parse_value(parse_value, _root);
}


bool Json::save_json(const char *path)const noexcept{

	FILE *fp = nullptr;
	if(fopen_s(&fp, path, "wb") || fp == nullptr){
		return false;
	}

	auto write_indent = [&](int indent)noexcept -> void {
		for(; indent > 0; --indent){
			fputs("  ", fp);
		}
	};
	auto write_string = [&](const std::string &string)noexcept -> void {
		fputc('"', fp);
		for(auto s = string.c_str(); *s; ++s){
			auto c = static_cast<uint8_t>(*s);
			switch(c){
			case '\"': fputs("\\\"", fp); break;
			case '\\': fputs("\\\\", fp); break;
			case '\b': fputs("\\b", fp); break;
			case '\f': fputs("\\f", fp); break;
			case '\n': fputs("\\n", fp); break;
			case '\r': fputs("\\r", fp); break;
			case '\t': fputs("\\t", fp); break;
			default:
				if(c < 0x20){
					fprintf(fp, "\\u%04x", c);
				}else{
					fprintf(fp, "%c", c);
				}
				break;
			}
		}
		fputc('"', fp);
	};
	auto write = [&](auto &&self, const Node &node, int indent=0)noexcept -> bool {
		switch(node._type){
		case Node::Type::Null:
			fputs("null", fp);
			break;
		case Node::Type::Bool:
			fputs(node.as_bool() ? "true" : "false", fp);
			break;
		case Node::Type::Int64:
			fprintf(fp, "%lld", node.as_int64());
			break;
		case Node::Type::Float64:
			fprintf(fp, "%.17g", node.as_float64());
			break;
		case Node::Type::String:
			write_string(node.as_string());
			break;
		case Node::Type::Object:
			if(auto object = node.as_object(); !object || object->empty()){
				fputs("{}", fp);
			}else{
				std::vector<std::pair<uint64_t, Node::string_t>> nodes;
				try{
					for(auto &child : *object){
						nodes.push_back({strtoull(child.first.c_str(), nullptr, 10), child.first});
					}
				}catch(...){
					return false;
				}
				std::sort(nodes.begin(), nodes.end(), [](const auto &a, const auto &b)noexcept{
					return a.first < b.first;
				});
				fputs("{\n", fp);
				bool is_not_first = false;
				for(auto &n : nodes){
					auto child = object->find(n.second);
					if(child == object->end()){
						return false;
					}
					if(is_not_first){
						fputs(",\n", fp);
					}else{
						is_not_first = true;
					}
					write_indent(indent + 1);
					write_string(child->first);
					fputs(": ", fp);
					if(!self(self, child->second, indent + 1)){
						return false;
					}
				}
				fputc('\n', fp);
				write_indent(indent);
				fputc('}', fp);
			}
			break;
		case Node::Type::Array:
			if(auto array = node.as_array(); !array || array->empty()){
				fputs("[]", fp);
			}else{
				fputs("[\n", fp);
				bool is_not_first = false;
				for(auto &child : *array){
					if(is_not_first){
						fputs(",\n", fp);
					}else{
						is_not_first = true;
					}
					write_indent(indent + 1);
					if(!self(self, child, indent + 1)){
						return false;
					}
				}
				fputc('\n', fp);
				write_indent(indent);
				fputc(']', fp);
			}
			break;

		}
		return true;
	};

	if(!write(write, _root)){
		fclose(fp);
		return false;
	}

	fclose(fp);

	return true;
}
