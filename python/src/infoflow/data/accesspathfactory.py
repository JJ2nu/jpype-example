from .accesspath import ArrayTaintType

from soot.ArrayType import ArrayType
from soot.Local import Local
from soot.PrimType import PrimType
from soot.RefLikeType import RefLikeType
from soot.RefType import RefType
from soot.SootField import SootField
from soot.Type import Type
from soot.Value import Value
from ...infoflow.sootir import soot_value

from soot.jimple.FieldRef import FieldRef
from ...infoflow.sootir.soot_value import SootInstanceFieldRef
from ...infoflow.sootir.soot_value import SootStaticFieldRef
from ...infoflow.sootir.soot_value import SootArrayRef
from ...infoflow.infoflowconfiguration import InfoflowConfiguration
import logging

from .accesspath import *
from ..util.typeutils import TypeUtils

logger = logging.getLogger(__file__)


class AccessPathFactory:

    def __init__(self, config: InfoflowConfiguration = None):
        self.config = config
        self.baseRegister = dict()

    class BasePair:

        def __init__(self, fields: list, types: list):
            self.fields = fields
            self.types = types

            if fields is None or len(fields) == 0:
                raise RuntimeError("A base must contain at least one field")

    def create_access_path(self, val, taint_sub_fields: bool, appending_fields=None, val_type=None, appending_field_types: ArrayTaintType =None,
                           cut_first_field: bool =False, reduce_bases: bool =True, array_taint_type: ArrayTaintType =ArrayTaintType.ContentsAndLength,
                           can_have_immutable_aliases: bool =False):
        if val is not None and not AccessPath.can_contain_value(val):
            logger.error("Access paths cannot be rooted in values of type {}", val.getClass().getName())
            return None

        if val is None and (appending_fields is None or len(appending_fields) == 0):
            return None

        access_path_config = self.config.access_path_configuration

        if not self.config.enable_type_checking:
            val_type = None
            appending_field_types = None

        if appending_fields is not None and appending_field_types is None:
            appending_field_types = []
            for i in range(0, len(appending_fields)):
                appending_field_types[i] = appending_fields[i].type

        if isinstance(val, FieldRef):
            ref = val

            if isinstance(val, SootInstanceFieldRef):
                iref = val
                value = iref.base
                base_type = value.type
            else:
                value = None
                base_type = None

            fields = SootField()
            fields[0] = ref.field

            if appending_fields is not None:
                fields.extend(appending_fields)

            field_types = list()
            field_types[0] = val_type if val_type is not None else fields[0].type

            if appending_field_types is not None:
                field_types.extend(appending_field_types)
        elif isinstance(val, SootArrayRef):
            ref = val
            value = ref.base
            base_type = value.type if val_type is None else val_type

            fields = appending_fields
            field_types = appending_field_types
        else:
            value = val
            base_type = (None if value is None else value.type) if val_type is None else val_type

            fields = appending_fields
            field_types = appending_field_types

        if access_path_config.accessPathLength == 0:
            fields = None
            field_types = None

        if cut_first_field and fields is not None and len(fields) > 0:
            new_fields = fields[1:]
            new_types = field_types[1:]
            fields = new_fields
            field_types = new_types

        if self.config.access_path_configuration.use_same_field_reduction and fields is not None and len(fields) > 1:
            for bucket_start in range(len(fields) - 2, -1, -1):
                repeat_pos = -1
                for i in range(bucket_start + 1, len(fields)):
                    if fields[i] == fields[bucket_start]:
                        repeat_pos = i
                        break
                repeat_len = repeat_pos - bucket_start
                if repeat_pos < 0:
                    continue

                matches = True
                for i in range(0, repeat_pos - bucket_start):
                    matches &= (repeat_pos + i < len(fields)) and fields[bucket_start + i] == fields[repeat_pos + i]
                if matches:
                    new_fields = fields[:bucket_start + 1]
                    new_fields.extend(fields[repeat_pos + 1:])
                    fields = new_fields

                    new_types = field_types[:bucket_start + 1]
                    new_types.extend(field_types[repeat_pos + 1:])
                    field_types = new_types
                    break

        if self.config.enable_type_checking:
            if value is not None and value.type != base_type:
                base_type = TypeUtils.get_more_precise_type( base_type, value.type )
                if base_type is None:
                    return None

                if fields is not None and len(fields) > 0 and not isinstance(base_type, ArrayType):
                    base_type = TypeUtils.get_more_precise_type( base_type, fields[0].getDeclaringClass().type )
                if base_type is None:
                    return None
            if fields is not None and field_types is not None:
                for i in range(0, len(fields)):
                    field_types[i] = TypeUtils.get_more_precise_type( field_types[i], fields[i].type )
                    if field_types[i] is None:
                        return None

                    if len(fields) > i + 1 and not isinstance(field_types[i], ArrayType):
                        field_types[i] = TypeUtils.get_more_precise_type( field_types[i],
                                                                          fields[i + 1].getDeclaringClass().type )
                    if field_types[i] is None:
                        return None

        if value is not None and isinstance(value.type, ArrayType):
            at = value.type
            if not isinstance(at.array_element_type, RefLikeType) and fields is not None and len(fields) > 0:
                return None

        if access_path_config.use_this_chain_reduction() and reduce_bases and fields is not None:
            for i in range(0, len(fields)):
                if fields[i].name.startsWith("this$"):
                    outer_class_name = fields[i].type.class_name

                    start_idx = -1
                    if value is not None and isinstance(value.type, RefType) and \
                            value.type.class_name() == outer_class_name:
                        start_idx = 0
                    else:
                        for j in range(0, i):
                            if isinstance(fields[j].type, RefType) and fields[j].type.class_name() == outer_class_name:
                                start_idx = j
                                break

                    if start_idx >= 0:
                        new_fields = fields[:start_idx]
                        new_field_types = field_types[:start_idx]

                        new_fields.extend(fields[i + 1:])
                        new_field_types.extend(field_types[i + 1:])

                        fields = new_fields
                        field_types = new_field_types
                        break

        recursive_cut_off = False
        if access_path_config.useRecursiveAccessPaths() and reduce_bases and fields is not None:
            ei = 1 if isinstance(val, SootStaticFieldRef) else 0
            while ei < len(fields):
                ei_type = base_type if ei == 0 else field_types[ei - 1]
                ej = ei
                while ej < len(fields):
                    if field_types[ej] == ei_type or fields[ej].type == ei_type:
                        new_fields = fields[:ei]
                        new_types = field_types[:ei]

                        if len(fields) > ej:
                            new_fields.extend(fields[ej + 1:])
                            new_types.extend(field_types[ej + 1:])

                        base = fields[ei:ej+1]
                        base_types = field_types[ei:ej+1]
                        self.register_base(ei_type, base, base_types)

                        fields = new_fields
                        field_types = new_types
                        recursive_cut_off = True
                    else:
                        ej += 1
                ei += 1

        if fields is not None:
            max_access_path_length = access_path_config.accessPathLength
            if max_access_path_length >= 0:
                field_num = min(max_access_path_length, len(fields))
                if len(fields) > field_num:
                    taint_sub_fields = True
                    cut_off_approximation = True
                else:
                    cut_off_approximation = recursive_cut_off

                if field_num == 0:
                    fields = None
                    field_types = None
                else:
                    new_fields = fields[:field_num]
                    new_field_types = field_types[:field_num]

                    fields = new_fields
                    field_types = new_field_types
            else:
                cut_off_approximation = recursive_cut_off
        else:
            cut_off_approximation = False
            fields = None
            field_types = None

        assert value is None or not (not isinstance(base_type, ArrayType) \
                                     and not TypeUtils.is_object_like_type( base_type ) \
                                     and isinstance(value.type, ArrayType))
        assert value is None or not isinstance(base_type, ArrayType) and not isinstance(value.type, ArrayType) \
               and not TypeUtils.is_object_like_type( value.type ) , "mismatch. was " + str( base_type ) + ", value was: " + str( value.type )

        if (fields is None and field_types is not None) or (fields is not None and field_types is None):
            raise RuntimeError("When there are fields, there must be field types and vice versa")
        if fields is not None and len(fields) != len(field_types):
            raise RuntimeError("Field and field type arrays must be of equal length")

        if isinstance(base_type, PrimType):
            if fields is not None:
                logger.warn("Primitive types cannot have fields: base_type=%s fields=%s" % (str(base_type),
                                                                                            str(fields)))
                return None
        if fields is not None:
            for i in range(0, len(fields) - 2):
                f = fields[i]
                field_type = f.type
                if isinstance(field_type, PrimType):
                    logger.warn("Primitive types cannot have fields: field=%s type=%s" % (str(f), str(field_type)))
                    return None

        return AccessPath(value, fields, base_type, field_types, taint_sub_fields, cut_off_approximation,
                           array_taint_type, can_have_immutable_aliases)

    def register_base(self, ei_type, base: list, base_types: list):
        assert len(base) == len(base_types)
        for i in range(0, len(base)):
            if base_types[i] == ei_type:
                new_base = base[:i + 1]
                new_types = base_types[:i + 1]

                base = new_base
                base_types = new_types
                break

        bases = self.baseRegister.setdefault(ei_type, set())
        bases.add(self.BasePair(base, base_types))

    def copy_with_new_value(self, original: AccessPath, val, new_type=None, cut_first_field: bool =False,
                            reduce_bases: bool=True, array_taint_type: ArrayTaintType =None):
        if new_type is None:
            new_type = original.base_type

        if array_taint_type is None:
            array_taint_type = original.arrayTaintType

        if original.val is not None and original.val == val and original.base_type == new_type \
                and original.arrayTaintType == array_taint_type:
            return original

        new_ap = self.create_access_path( val, original.fields, new_type, original.field_types, original.taintSubFields,
                                          cut_first_field, reduce_bases, array_taint_type,
                                          original.can_have_immutable_aliases )

        if new_ap is not None and new_ap == original:
            return original
        else:
            return new_ap

    def merge(self, ap1: AccessPath, ap2: AccessPath):
        return self.append_fields( ap1, ap2.fields, ap2.field_types, ap2.taintSubFields )

    def append_fields(self, original:AccessPath, ap_fields: list, ap_field_types: list, taint_sub_fields: bool):
        offset = 0 if original.fields is None else len(original.fields)
        fields = list()
        field_types = list()
        if original.fields is not None:
            fields = original.fields[:len(original.fields)]
            field_types = original.field_types[:len( original.field_types )]
        if ap_fields is not None and len(ap_fields) > 0:
            fields = ap_fields[offset:offset + len(ap_fields)]
            field_types = ap_field_types[offset:offset + len(ap_field_types)]

        return self.create_access_path( original.val, fields, original.base_type, field_types, taint_sub_fields, False,
                                        True, original.arrayTaintType )